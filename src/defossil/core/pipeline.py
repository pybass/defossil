"""The one background thread that spends money: reviewing full batches and making reports.

Nothing else creates corrections or reports, so their tables are strictly append-only and no step races another.
Filling the archive is not its job — the message service's importer worker does that on its own clock.
"""

import logging
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel

from defossil.core.errors import AiError
from defossil.core.service import Service

if TYPE_CHECKING:
    from defossil.core.core import Core

logger = logging.getLogger(__name__)

SWEEP_INTERVAL = 300.0  # seconds between sweeps; also the retry delay after a failed backend call


class PipelineStatus(BaseModel):
    """What the pipeline's last sweep did; replaced whole, so a reader never sees a half-written one."""

    swept_at: datetime  # when the sweep finished; the interval to the next one starts here
    reviewed: int  # messages whose batches succeeded this sweep
    corrections: int  # corrections those batches appended
    reports: int  # reports made this sweep
    error: str | None = None  # first failure; the sweep stopped there and the next one retries


class Pipeline(Service):
    """Owner of the worker; each sweep runs the steps in order, so a report never races the reviews it should cover."""

    def __init__(self, core: Core) -> None:
        """Bind to Core; the worker is not started here."""
        super().__init__(core)
        self._stopping = threading.Event()
        self._wake = threading.Event()  # cuts the between-sweeps wait short; set on stop and on enable
        self._enabled = threading.Event()  # cleared = the worker idles, nothing runs; not persisted, every launch starts off
        self._worker: threading.Thread | None = None
        self._status: PipelineStatus | None = None  # written only by the worker; None until its first sweep finishes

    def on_start(self) -> None:
        """Start the one worker."""
        self._worker = threading.Thread(target=self._run, name="pipeline", daemon=True)
        self._worker.start()

    def on_stop(self) -> None:
        """Let the worker finish the step it is on, then wait for it."""
        if self._worker is None:
            return
        self._stopping.set()
        self._wake.set()
        # No timeout: joining is what keeps the worker off the database while Core closes it.
        self._worker.join()
        self._worker = None

    def get_status(self) -> PipelineStatus | None:
        """Return what the last sweep did, or None while the first one is still running."""
        return self._status

    def is_enabled(self) -> bool:
        """Whether sweeps run at all; off pauses reviewing and reporting, importing is not this worker's."""
        return self._enabled.is_set()

    def sweep_now(self) -> None:
        """Cut the between-sweeps wait short so a sweep starts immediately; a no-op while the pipeline is disabled."""
        logger.info("pipeline: sweep requested")
        self._wake.set()

    def set_enabled(self, enabled: bool) -> None:
        """Turn the pipeline on or off; turning on wakes the worker so a sweep starts now, not up to an interval later."""
        logger.info(f"pipeline: {'enabled' if enabled else 'disabled'}")
        if enabled:
            self._enabled.set()
            self._wake.set()
        else:
            self._enabled.clear()

    def _run(self) -> None:
        """Sweep on the interval while enabled; one sweep's failure must not end the loop."""
        while not self._stopping.is_set():
            if self._enabled.is_set():
                try:
                    self._status = self._sweep()
                except Exception as e:
                    logger.exception("pipeline: sweep failed")
                    error = f"{type(e).__name__}: {e}"
                    self._status = PipelineStatus(swept_at=datetime.now(UTC), reviewed=0, corrections=0, reports=0, error=error)
            self._wake.wait(SWEEP_INTERVAL)
            self._wake.clear()

    def _sweep(self) -> PipelineStatus:
        """Run the pipeline once: review full batches, making each report as soon as its window fills."""
        services = self.core.services
        reviewed = corrections = reports = 0
        error: str | None = None
        # The enabled flag is re-checked per batch and per report, so switching off mid-sweep stops after the one in flight.
        while self._enabled.is_set() and not self._stopping.is_set():
            # The batch size is re-read per batch, so an edit applies without a restart — but read once per
            # iteration: two reads could straddle an edit and desync the fetch limit from the full-batch check.
            batch_size = services.setting.get_settings().messages_per_review
            batch = services.message.get_pending_messages(batch_size)
            if len(batch) < batch_size:
                break  # a remainder below a full batch waits for more messages
            try:
                added = services.correction.review_messages(batch)
            except AiError as e:
                # Resending now would hammer a down backend; the next sweep retries.
                logger.exception("pipeline: batch failed")
                error = str(e)
                break
            reviewed += len(batch)
            corrections += added
            logger.info(f"pipeline: reviewed {len(batch)} messages, {added} corrections")
            reports += self._make_due_reports()
        if error is None:
            # Windows can be due without a full batch reviewed: a lowered corrections_per_report, or a report that
            # failed after its reviews succeeded.
            reports += self._make_due_reports()
        return PipelineStatus(
            swept_at=datetime.now(UTC), reviewed=reviewed, corrections=corrections, reports=reports, error=error
        )

    def _make_due_reports(self) -> int:
        """Make a report for every window of unreported corrections that has filled; return how many were made."""
        services = self.core.services
        made = 0
        while self._enabled.is_set() and not self._stopping.is_set():
            if services.report.count_unreported_corrections() < services.setting.get_settings().corrections_per_report:
                break
            report = services.report.make_report()
            logger.info(f"pipeline: made report {report.id}")
            made += 1
        return made

import logging
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from defossil.core.errors import BusyError, InvalidOperationError, NotFoundError
from defossil.core.features.correction.models import CorrectionCategory
from defossil.core.features.report.models import Report, ReportChatSession, ReportChatTurn, ReportStatus
from defossil.core.service import Service
from defossil.core.worker import Worker

if TYPE_CHECKING:
    from defossil.core.core import Core

logger = logging.getLogger(__name__)

REPORT_INTERVAL = 300.0  # seconds between report runs; also the retry delay after a failed backend call

# The whole input must stay under ~20-30k words: repeat-counting degrades long before the model's context limit.
# Corrections need no cap — the window is a fixed count — but a window's message span is unbounded
# (clean stretches add messages without corrections).
# Clean messages are input on purpose: the report judges style the review never flagged, message-wide and across
# messages, which no correction fragment can show.
MESSAGES_CAP = 300
PREVIOUS_REPORTS = 2


class ReportService(Service):
    """Owner of `reports` and of the chat under each one (`report_chat_sessions`, `report_chat_turns`).

    Only the report worker owned here makes a report, and one is never regenerated. The chat pool serves the
    user's questions beside it: the turns are the record of a chat, the session row only the CLI session the
    next turn resumes, replaced whenever a resume fails and the turns are replayed into a fresh one.
    """

    def __init__(self, core: Core) -> None:
        """Bind to Core; neither the worker nor the chat pool is started here."""
        super().__init__(core)
        self._report_worker = Worker("report", REPORT_INTERVAL, self._report_run)
        self._report_status: ReportStatus | None = None  # written only by the worker; None until a run with auto AI on finishes
        self._chat_pending: set[int] = set()  # report ids with a turn queued or being asked right now
        self._chat_lock = threading.Lock()
        self._chat_pool: ThreadPoolExecutor | None = None

    def on_start(self) -> None:
        self._report_worker.start()
        self._chat_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="chat")  # backend calls at once

    def on_stop(self) -> None:
        """Let the worker finish the report it is on, drop the queued turns and wait out the running ones."""
        self._report_worker.stop()
        if self._chat_pool is not None:
            # No timeout, same as the worker: the pool must be off the database before Core closes it.
            self._chat_pool.shutdown(wait=True, cancel_futures=True)
            self._chat_pool = None
            self._chat_pending.clear()  # cancelled turns never reach the task's cleanup

    def get_report_status(self) -> ReportStatus | None:
        """Return what the last report run did; None until a run with auto AI on has finished."""
        return self._report_status

    def report_now(self) -> None:
        """Cut the between-runs wait short so a report run starts immediately; a no-op while auto AI is off."""
        logger.info("report: run requested")
        self._report_worker.wake()

    def _report_run(self) -> None:
        """One worker run: make due reports if auto AI is on; a failure lands in the status, never on the worker's loop."""
        if not self.core.services.ai.is_auto_ai():
            return
        try:
            self._report_status = self._make_due_reports()
        except Exception as e:
            logger.exception("report: run failed")
            error = f"{type(e).__name__}: {e}"
            self._report_status = ReportStatus(reported_at=datetime.now(UTC), reports=0, error=error)

    def _make_due_reports(self) -> ReportStatus:
        """Make a report for every window of unreported corrections that has filled."""
        made = 0
        # Auto AI is re-checked per report, so switching it off mid-run stops after the one in flight.
        while self.core.services.ai.is_auto_ai() and not self._report_worker.stopping:
            if self.count_unreported_corrections() < self.core.services.setting.get_settings().corrections_per_report:
                break
            report = self.make_report()
            logger.info(f"report: made report {report.id}")
            made += 1
        return ReportStatus(reported_at=datetime.now(UTC), reports=made)

    def count_unreported_corrections(self) -> int:
        """Count the corrections past the last report's window — filling `corrections_per_report` triggers the next one."""
        last = next(iter(self.get_last_reports(1)), None)
        return self.core.services.correction.count_corrections_after(last.last_correction_id if last else 0)

    def make_report(self) -> Report:
        """Ask the LLM for a lesson over the next `corrections_per_report` corrections, store it, and return it.

        The window starts after the previous report's ranges and closes on the correction that fills the count;
        raises when fewer have accumulated. A failed backend call raises and stores nothing.
        """
        count = self.core.services.setting.get_settings().corrections_per_report
        previous = self.get_last_reports(PREVIOUS_REPORTS)
        window = self.core.services.correction.find_report_window(previous[0].last_correction_id if previous else 0, count)
        if window is None:
            raise InvalidOperationError(f"Fewer than {count} corrections since the last report")
        corrections = self.core.services.correction.get_corrections_in_id_range(
            window.first_correction_id, window.last_correction_id
        )
        # Two tiers under the cap: every correction-carrying message (always inside the range — the range is defined
        # by them), then the newest clean messages as filler. The cap may only trim clean filler, never a message the
        # corrections quote.
        carrying_ids = {c.message_id for c in corrections}
        carrying = self.core.services.message.get_messages_by_ids(sorted(carrying_ids))
        recent = self.core.services.message.get_reviewed_messages_in_range(
            window.first_message_id, window.last_message_id, MESSAGES_CAP
        )
        clean = [m for m in recent if m.id not in carrying_ids]
        fill_count = max(0, MESSAGES_CAP - len(carrying))
        messages = sorted(carrying + clean[max(0, len(clean) - fill_count) :], key=lambda m: m.id)
        previous_corrections = (
            self.core.services.correction.get_corrections_in_id_range(
                previous[0].first_correction_id, previous[0].last_correction_id
            )
            if previous
            else []
        )
        # Typos are one-off slips by definition and the report ignores one-offs, so they never make the input.
        text = self.core.services.ai.send_report_prompt(
            corrections=[c for c in corrections if c.category != CorrectionCategory.TYPO],
            previous_corrections=[c for c in previous_corrections if c.category != CorrectionCategory.TYPO],
            messages=[m.text for m in messages],
            previous_reports=[r.text for r in previous],
        )
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO reports (created_at, first_message_id, last_message_id, first_correction_id, last_correction_id, text)
                VALUES (?,?,?,?,?,?)
                """,
                (
                    datetime.now(UTC).isoformat(),
                    window.first_message_id,
                    window.last_message_id,
                    window.first_correction_id,
                    window.last_correction_id,
                    text,
                ),
            )
        return self.get_last_reports(1)[0]

    def get_last_reports(self, n: int | None = None) -> list[Report]:
        """Return the reports newest first — all of them, or only *n* when given."""
        rows = self.db.conn.execute("SELECT * FROM reports ORDER BY id DESC LIMIT IFNULL(?, -1)", (n,))
        return [Report.from_row(row) for row in rows]

    def count_unacknowledged_reports(self) -> int:
        """Count the reports the user has not acknowledged yet — the nav's unread badge."""
        row = self.db.conn.execute("SELECT COUNT(*) FROM reports WHERE acknowledged = 0").fetchone()
        return int(row[0])

    def acknowledge_report(self, report_id: int) -> None:
        """Set the acknowledged flag: the user has read the lesson."""
        with self.db.transaction() as conn:
            if conn.execute("UPDATE reports SET acknowledged = 1 WHERE id = ?", (report_id,)).rowcount == 0:
                raise NotFoundError(f"No report {report_id}")

    def unacknowledge_report(self, report_id: int) -> None:
        """Take the acknowledged flag back off — the undo for a mis-click."""
        with self.db.transaction() as conn:
            if conn.execute("UPDATE reports SET acknowledged = 0 WHERE id = ?", (report_id,)).rowcount == 0:
                raise NotFoundError(f"No report {report_id}")

    def delete_last_report(self) -> int:
        """Delete the newest report and return its id — the one developer exception to the append-only rule.

        Reopens the report's correction window, so the worker rebuilds it on a later run — how a prompt change
        is tried on the same data.
        """
        last = next(iter(self.get_last_reports(1)), None)
        if last is None:
            raise NotFoundError("No reports")
        # Held across the delete: a turn admitted meanwhile would write its answer under a rebuilt report's id.
        with self._chat_lock:
            if last.id in self._chat_pending:
                raise BusyError(f"Report {last.id} has a chat turn in flight")
            with self.db.transaction() as conn:
                conn.execute("DELETE FROM report_chat_turns WHERE report_id = ?", (last.id,))
                conn.execute("DELETE FROM report_chat_sessions WHERE report_id = ?", (last.id,))
                conn.execute("DELETE FROM reports WHERE id = ?", (last.id,))
        return last.id

    def get_report(self, report_id: int) -> Report:
        row = self.db.conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"No report {report_id}")
        return Report.from_row(row)

    def ask(self, report_id: int, question: str) -> None:
        """Queue one question for the report's chat; the answer lands as a turn when the pool is done with it.

        Raises NotFoundError for a bad report, InvalidOperationError for a blank question, and BusyError while
        the report already has a turn in flight — the caller keeps the question and tries again later.
        """
        self.get_report(report_id)  # a bad id must 404 here, not fail later in the pool's log
        if not question.strip():
            raise InvalidOperationError("The question is empty")
        if self._chat_pool is None:
            raise RuntimeError("The chat pool is not running")
        with self._chat_lock:
            if report_id in self._chat_pending:
                raise BusyError(f"Report {report_id} has a chat turn in flight")
            self._chat_pending.add(report_id)
        try:
            self._chat_pool.submit(self._run_turn, report_id, question.strip())
        except BaseException:
            with self._chat_lock:
                self._chat_pending.discard(report_id)
            raise

    def get_pending_chat_ids(self) -> set[int]:
        """Return the report ids with a chat turn queued or being asked right now."""
        with self._chat_lock:
            return set(self._chat_pending)

    def get_chat_turns(self, report_id: int, after_id: int = 0) -> list[ReportChatTurn]:
        """Return the report's turns in order, only those past *after_id* — the page's poll cursor."""
        rows = self.db.conn.execute(
            "SELECT * FROM report_chat_turns WHERE report_id = ? AND id > ? ORDER BY id", (report_id, after_id)
        )
        return [ReportChatTurn.from_row(row) for row in rows]

    def get_chat_session(self, report_id: int) -> ReportChatSession | None:
        row = self.db.conn.execute("SELECT * FROM report_chat_sessions WHERE report_id = ?", (report_id,)).fetchone()
        return ReportChatSession.from_row(row) if row is not None else None

    def _run_turn(self, report_id: int, question: str) -> None:
        """One pool task: resume the chat's session or open one over the stored turns, then store the turn.

        A failed turn is dropped — its error is in the ai calls log. A failure after the backend answered leaves
        the CLI session ahead of the record, so the session row goes too: the next turn replays from the turns.
        """
        # One settings snapshot for the whole turn, so the session is resumed and saved under the same backend.
        settings = self.core.services.setting.get_settings()
        answered = False
        try:
            report = self.get_report(report_id)
            session = self.get_chat_session(report_id)
            answer: str | None = None
            session_id: str | None = None
            if session is not None and session.backend == settings.ai_backend:
                try:
                    answer, session_id = self.core.services.ai.send_chat_prompt(
                        settings, report.text, [], question, session.session_id
                    )
                except subprocess.CalledProcessError:
                    # The CLI no longer has the session (Claude Code drops transcripts after ~30 days) or refused
                    # it. One call wasted; the replay below gives the report a fresh session.
                    logger.info(f"chat: report {report_id}: resume of {session.session_id} failed, replaying")
            if answer is None or session_id is None:
                turns = self.get_chat_turns(report_id)
                answer, session_id = self.core.services.ai.send_chat_prompt(settings, report.text, turns, question, None)
            answered = True
            now = datetime.now(UTC).isoformat()
            with self.db.transaction() as conn:
                if session is None or session.session_id != session_id:
                    conn.execute(
                        """
                        INSERT INTO report_chat_sessions (report_id, backend, session_id, created_at) VALUES (?,?,?,?)
                        ON CONFLICT(report_id) DO UPDATE SET backend = excluded.backend,
                            session_id = excluded.session_id, created_at = excluded.created_at
                        """,
                        (report_id, settings.ai_backend, session_id, now),
                    )
                conn.execute(
                    "INSERT INTO report_chat_turns (report_id, question, answer, created_at) VALUES (?,?,?,?)",
                    (report_id, question, answer, now),
                )
        except Exception:
            logger.exception(f"chat: report {report_id} failed")
            if answered:
                with self.db.transaction() as conn:
                    conn.execute("DELETE FROM report_chat_sessions WHERE report_id = ?", (report_id,))
        finally:
            # Removed only now, after the write: while the turn runs, the id must still read as pending.
            with self._chat_lock:
                self._chat_pending.discard(report_id)

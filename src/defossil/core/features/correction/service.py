"""Running one review batch, and owning the stored corrections it appends."""

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from defossil.core.errors import NotFoundError
from defossil.core.features.correction.models import Correction, CorrectionCategory
from defossil.core.features.message.models import Message
from defossil.core.features.report.models import ReportWindow
from defossil.core.service import Service

if TYPE_CHECKING:
    from defossil.core.core import Core

logger = logging.getLogger(__name__)


class CorrectionService(Service):
    """Owner of the `corrections` table: the review batch that fills it, and the aggregates.

    Rows are append-only — a message is reviewed once, ever — so correction ids are stable and a report's
    stored id range stays true. Only the pipeline thread calls `review_messages`; the explain pool owned
    here holds the only other threads that talk to the backend, serving queued asks concurrently.
    """

    def __init__(self, core: Core) -> None:
        """Bind to Core; the explain pool is not started here."""
        super().__init__(core)
        self._explain_pending: set[int] = set()  # correction ids queued or being asked right now
        self._explain_lock = threading.Lock()
        self._explain_pool: ThreadPoolExecutor | None = None

    def on_start(self) -> None:
        """Start the explain pool."""
        self._explain_pool = ThreadPoolExecutor(max_workers=10, thread_name_prefix="explain")  # backend calls at once

    def on_stop(self) -> None:
        """Drop the queued asks and wait out the running ones — the pool must be off the database before Core closes it."""
        if self._explain_pool is None:
            return
        self._explain_pool.shutdown(wait=True, cancel_futures=True)
        self._explain_pool = None
        self._explain_pending.clear()  # cancelled asks never reach the task's cleanup

    def request_explanation(self, correction_id: int, question: str) -> None:
        """Queue one correction for the explain pool; a repeat ask while one is queued or running is a no-op."""
        self.get_correction(correction_id)  # a bad id must 404 here, not fail later in the pool's log
        if self._explain_pool is None:
            raise RuntimeError("The explain pool is not running")
        with self._explain_lock:
            if correction_id in self._explain_pending:
                return
            self._explain_pending.add(correction_id)
        self._explain_pool.submit(self._run_explain, correction_id, question)

    def get_pending_explanation_ids(self) -> set[int]:
        """Return the correction ids queued or being explained right now."""
        with self._explain_lock:
            return set(self._explain_pending)

    def get_extra_explanations(self, correction_ids: list[int]) -> dict[int, str | None]:
        """Return the stored extra explanation per given correction id; unknown ids are absent."""
        rows = self.db.conn.execute(
            "SELECT id, extra_explanation FROM corrections WHERE id IN (SELECT value FROM json_each(?))",
            (json.dumps(correction_ids),),
        )
        return {row["id"]: row["extra_explanation"] for row in rows}

    def _run_explain(self, correction_id: int, question: str) -> None:
        """One pool task: ask the backend and store the answer; a failed ask is dropped — its error is in the ai calls log."""
        try:
            correction = self.get_correction(correction_id)
            message = self.core.services.message.get_message(correction.message_id)
            answer = self.core.services.ai.send_explain_prompt(correction, message.text, question)
            with self.db.transaction() as conn:
                conn.execute("UPDATE corrections SET extra_explanation = ? WHERE id = ?", (answer, correction_id))
        except Exception:
            logger.exception(f"explain: correction {correction_id} failed")
        finally:
            # Removed only now, after the write: while the ask runs, the id must still read as pending.
            with self._explain_lock:
                self._explain_pending.discard(correction_id)

    def review_messages(self, messages: list[Message]) -> int:
        """Send one batch, append its corrections and stamp the messages reviewed in one transaction; returns the count.

        A failed call or an unreadable answer raises AiError and stores nothing — the messages stay pending; the raw
        reply is in the ai query log either way.
        """
        answers = self.core.services.ai.send_review_prompt({message.id: message.text for message in messages})
        rows = [(c.message_id, c.category, c.original, c.corrected, c.explanation) for c in answers]
        with self.db.transaction() as conn:
            conn.executemany(
                "INSERT INTO corrections (message_id, category, original, corrected, explanation) VALUES (?,?,?,?,?)",
                rows,
            )
            self.core.services.message.mark_messages_reviewed([m.id for m in messages])
        return len(rows)

    def count_corrections(self, category: CorrectionCategory | None = None, acknowledged: bool | None = None) -> int:
        """Count the stored corrections; a None filter means no filter."""
        row = self.db.conn.execute(
            """
            SELECT COUNT(*) FROM corrections
            WHERE (:category IS NULL OR category = :category) AND (:acknowledged IS NULL OR acknowledged = :acknowledged)
            """,
            {"category": category, "acknowledged": acknowledged},
        ).fetchone()
        return int(row[0])

    def count_corrections_by_category(self, acknowledged: bool | None = None) -> list[tuple[CorrectionCategory, int]]:
        """Count the stored corrections per category, most frequent first; a None filter means no filter."""
        rows = self.db.conn.execute(
            """
            SELECT category, COUNT(*) AS n FROM corrections
            WHERE :acknowledged IS NULL OR acknowledged = :acknowledged
            GROUP BY category ORDER BY n DESC, category
            """,
            {"acknowledged": acknowledged},
        )
        return [(CorrectionCategory(row["category"]), int(row["n"])) for row in rows]

    def count_corrections_by_message(self, message_ids: list[int]) -> dict[int, int]:
        """Count each given message's corrections, keyed by message id; ids without any are absent."""
        rows = self.db.conn.execute(
            """
            SELECT message_id, COUNT(*) AS n FROM corrections
            WHERE message_id IN (SELECT value FROM json_each(?)) GROUP BY message_id
            """,
            (json.dumps(message_ids),),
        )
        return {row["message_id"]: int(row["n"]) for row in rows}

    def get_corrections(
        self, category: CorrectionCategory | None, acknowledged: bool | None, limit: int, offset: int
    ) -> list[Correction]:
        """Return one page of corrections, most recently typed messages first; a None filter means no filter."""
        rows = self.db.conn.execute(
            """
            SELECT c.*, m.typed_at FROM corrections c JOIN messages m ON m.id = c.message_id
            WHERE (:category IS NULL OR c.category = :category)
                AND (:acknowledged IS NULL OR c.acknowledged = :acknowledged)
            ORDER BY m.typed_at DESC, c.id DESC LIMIT :limit OFFSET :offset
            """,
            {"category": category, "acknowledged": acknowledged, "limit": limit, "offset": offset},
        )
        return [Correction.from_row(row) for row in rows]

    def get_correction(self, correction_id: int) -> Correction:
        """Return one stored correction."""
        row = self.db.conn.execute(
            "SELECT c.*, m.typed_at FROM corrections c JOIN messages m ON m.id = c.message_id WHERE c.id = ?",
            (correction_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"No correction {correction_id}")
        return Correction.from_row(row)

    def get_message_corrections(self, message_id: int) -> list[Correction]:
        """Return one message's corrections; the message page shows them under the text."""
        rows = self.db.conn.execute(
            """
            SELECT c.*, m.typed_at FROM corrections c JOIN messages m ON m.id = c.message_id
            WHERE c.message_id = ? ORDER BY c.id
            """,
            (message_id,),
        )
        return [Correction.from_row(row) for row in rows]

    def count_corrections_after(self, correction_id: int) -> int:
        """Count the corrections with id above *correction_id* (0 = all) — what the next report would cover."""
        row = self.db.conn.execute("SELECT COUNT(*) FROM corrections WHERE id > ?", (correction_id,)).fetchone()
        return int(row[0])

    def get_corrections_in_id_range(self, first_id: int, last_id: int) -> list[Correction]:
        """Return the corrections with id in [*first_id*, *last_id*], oldest first; a report window's input."""
        rows = self.db.conn.execute(
            """
            SELECT c.*, m.typed_at FROM corrections c JOIN messages m ON m.id = c.message_id
            WHERE c.id >= :first_id AND c.id <= :last_id ORDER BY c.id
            """,
            {"first_id": first_id, "last_id": last_id},
        )
        return [Correction.from_row(row) for row in rows]

    def find_report_window(self, after_correction_id: int, count: int) -> ReportWindow | None:
        """Return the window of *count* corrections after *after_correction_id*; None = fewer have accumulated."""
        last = self.db.conn.execute(
            "SELECT id, message_id FROM corrections WHERE id > :after ORDER BY id LIMIT 1 OFFSET :count - 1",
            {"after": after_correction_id, "count": count},
        ).fetchone()
        if last is None:
            return None
        first = self.db.conn.execute(
            "SELECT id, message_id FROM corrections WHERE id > ? ORDER BY id LIMIT 1", (after_correction_id,)
        ).fetchone()
        return ReportWindow(
            first_correction_id=first["id"],
            last_correction_id=last["id"],
            first_message_id=first["message_id"],
            last_message_id=last["message_id"],
        )

    def acknowledge_correction(self, correction_id: int) -> None:
        """Flag one correction as read and understood by the user."""
        with self.db.transaction() as conn:
            if conn.execute("UPDATE corrections SET acknowledged = 1 WHERE id = ?", (correction_id,)).rowcount == 0:
                raise NotFoundError(f"No correction {correction_id}")

    def unacknowledge_correction(self, correction_id: int) -> None:
        """Take the acknowledged flag back off — the undo for a mis-click."""
        with self.db.transaction() as conn:
            if conn.execute("UPDATE corrections SET acknowledged = 0 WHERE id = ?", (correction_id,)).rowcount == 0:
                raise NotFoundError(f"No correction {correction_id}")

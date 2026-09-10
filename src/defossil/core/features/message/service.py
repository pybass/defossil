import json
import logging
import sqlite3
import threading
from typing import TYPE_CHECKING, assert_never

from defossil.core.errors import NotFoundError
from defossil.core.features.message.models import Message, MessageStatus, Source
from defossil.core.features.message.sources import claude_code, codex
from defossil.core.service import Service
from defossil.core.worker import Worker

if TYPE_CHECKING:
    from defossil.core.core import Core

logger = logging.getLogger(__name__)

IMPORT_INTERVAL = 300.0  # seconds between import runs; also the retry delay after a failed one

MAX_CHARS = 2000  # past this every message in the archive turned out to be pasted output, not typing
MIN_WORDS = 4  # too little to judge grammar on
DUPLICATE_DAYS = 7  # the same text again within this window is a paste; further apart it is a habit and gets reviewed


def duplicate_key(text: str) -> str:
    """Letters and digits only: a paste into another agent's TUI comes back with changed indentation and list markers."""
    return "".join(ch for ch in text if ch.isalnum())


def classify_text(text: str) -> MessageStatus:
    """Judge one archived message; only PENDING goes on to the reviewer, the rest is skipped whole.

    It only judges: the text is never rewritten, because a correction must quote a fragment of what the user really typed.
    """
    if not any(ch.isalpha() for ch in text):
        return MessageStatus.NO_PROSE
    # Any Cyrillic at all: such a message is not English, and the English inside it is quoted, not written by the
    # user. A ratio threshold does not work — a non-English message with a big pasted English log passes any ratio.
    if any("Ѐ" <= ch <= "ӿ" for ch in text):  # Unicode Cyrillic block
        return MessageStatus.NON_ENGLISH
    if len(text) > MAX_CHARS:
        return MessageStatus.TOO_LONG
    if len(text.split()) < MIN_WORDS:
        return MessageStatus.TOO_SHORT
    return MessageStatus.PENDING


class MessageService(Service):
    """Owner of the `messages` table and of the importer worker that keeps it filled."""

    def __init__(self, core: Core) -> None:
        """Bind to Core; the worker is not started here."""
        super().__init__(core)
        self._import_worker = Worker("importer", IMPORT_INTERVAL, self.import_messages)
        self._import_lock = threading.Lock()  # import_messages is serial: the worker and any direct caller queue here

    def on_start(self) -> None:
        """Start the importer worker; it always runs — which sources it reads is settings, not state."""
        self._import_worker.start()

    def on_stop(self) -> None:
        """Let the worker finish the run it is on, then wait for it."""
        self._import_worker.stop()

    def import_messages(self) -> None:
        """Run one import — rescan every enabled source, archive what is new, classify it.

        Serial: concurrent callers queue on the lock. A failure is logged, not raised — the worker's next run retries.
        There is no incremental state: a full rescan is cheap and the source key makes overlapping files safe.
        """
        with self._import_lock:
            try:
                settings = self.core.services.setting.get_settings()
                enabled = {Source.CLAUDE_CODE: settings.source_claude_code_enabled, Source.CODEX: settings.source_codex_enabled}
                for source in [s for s in Source if enabled[s]]:
                    if new := self._import_source(source):
                        logger.info(f"importer: {source} archived {new} new messages")
                self._classify_new_messages()
            except Exception:
                logger.exception("importer: run failed")

    def _classify_new_messages(self) -> None:
        """Judge the `new` messages; the verdict is stamped once and only moves forward (pending -> reviewed)."""
        rows = self.db.conn.execute("SELECT * FROM messages WHERE status = 'new'").fetchall()
        statuses = {row["id"]: classify_text(row["text"]) for row in rows}
        pending = [row for row in rows if statuses[row["id"]] is MessageStatus.PENDING]
        duplicate_ids = self._find_duplicates(pending)
        with self.db.transaction() as conn:
            conn.executemany("UPDATE messages SET status = ? WHERE id = ?", [(s, mid) for mid, s in statuses.items()])
            conn.executemany("UPDATE messages SET status = 'duplicate' WHERE id = ?", [(mid,) for mid in duplicate_ids])

    def _find_duplicates(self, pending: list[sqlite3.Row]) -> list[int]:
        """Ids of the would-be pending rows whose key was already typed within DUPLICATE_DAYS before them.

        Only the batch's would-be pending messages are checked, since "too-short" says more than "duplicate". The older
        copy is the earliest typed_at, not the lowest id — a rescan inserts sources one after another, so ids do not
        follow time across sources. Its status does not matter, so a copy inside this same batch counts too. The window
        is one query over everything typed since DUPLICATE_DAYS before the batch's earliest message; keys are computed
        in Python because SQL cannot strip spacing and punctuation.
        """
        if not pending:
            return []
        earliest = min(row["typed_at"] for row in pending)  # typed_at strings compare directly (all UTC ISO)
        rows = self.db.conn.execute(
            "SELECT id, text FROM messages WHERE julianday(typed_at) >= julianday(?) - ? ORDER BY typed_at, id",
            (earliest, DUPLICATE_DAYS),
        )
        pending_ids = {row["id"] for row in pending}
        seen: set[str] = set()
        duplicates: list[int] = []
        for row in rows:
            key = duplicate_key(row["text"])
            if key in seen and row["id"] in pending_ids:
                duplicates.append(row["id"])
            seen.add(key)
        return duplicates

    def get_message(self, message_id: int) -> Message:
        row = self.db.conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"No message {message_id}")
        return Message.from_row(row)

    def get_messages(self, status: MessageStatus | None, source: Source | None, limit: int, offset: int) -> list[Message]:
        """Return one page of the archive, newest first; a None filter means no filter."""
        rows = self.db.conn.execute(
            """
            SELECT * FROM messages
            WHERE (:status IS NULL OR status = :status) AND (:source IS NULL OR source = :source)
            ORDER BY typed_at DESC LIMIT :limit OFFSET :offset
            """,
            {"status": status, "source": source, "limit": limit, "offset": offset},
        )
        return [Message.from_row(row) for row in rows]

    def get_pending_messages(self, limit: int) -> list[Message]:
        """Return up to *limit* `pending` messages, in id order — the order corrections are created in."""
        rows = self.db.conn.execute("SELECT * FROM messages WHERE status = 'pending' ORDER BY id LIMIT ?", (limit,))
        return [Message.from_row(row) for row in rows]

    def mark_messages_reviewed(self, message_ids: list[int]) -> None:
        """Stamp the batch `reviewed`; call inside an open transaction so the stamp lands with the corrections."""
        self.db.conn.executemany("UPDATE messages SET status = 'reviewed' WHERE id = ?", [(mid,) for mid in message_ids])

    def get_messages_by_ids(self, message_ids: list[int]) -> list[Message]:
        """Return the messages with these ids, in id order; unknown ids are silently absent."""
        rows = self.db.conn.execute(
            "SELECT * FROM messages WHERE id IN (SELECT value FROM json_each(?)) ORDER BY id", (json.dumps(message_ids),)
        )
        return [Message.from_row(row) for row in rows]

    def get_reviewed_messages_in_range(self, first_id: int, last_id: int, limit: int) -> list[Message]:
        """Return the newest `reviewed` messages with id in [*first_id*, *last_id*], oldest first, at most *limit*."""
        rows = self.db.conn.execute(
            """
            SELECT * FROM (
                SELECT * FROM messages WHERE status = 'reviewed' AND id >= :first_id AND id <= :last_id
                ORDER BY id DESC LIMIT :limit
            ) ORDER BY id
            """,
            {"first_id": first_id, "last_id": last_id, "limit": limit},
        )
        return [Message.from_row(row) for row in rows]

    def count_reviewed_messages_in_range(self, first_id: int, last_id: int) -> int:
        """Count the `reviewed` messages with id in [*first_id*, *last_id*] — a report window's rate denominator."""
        row = self.db.conn.execute(
            "SELECT COUNT(*) FROM messages WHERE status = 'reviewed' AND id >= ? AND id <= ?", (first_id, last_id)
        ).fetchone()
        return int(row[0])

    def count_messages(self, status: MessageStatus | None = None, source: Source | None = None) -> int:
        """Count archived messages matching the filters; a None filter means no filter."""
        row = self.db.conn.execute(
            """
            SELECT COUNT(*) FROM messages
            WHERE (:status IS NULL OR status = :status) AND (:source IS NULL OR source = :source)
            """,
            {"status": status, "source": source},
        ).fetchone()
        return int(row[0])

    def count_messages_by_source(self) -> dict[str, int]:
        """Count the archive per source, largest first."""
        rows = self.db.conn.execute("SELECT source, COUNT(*) AS n FROM messages GROUP BY source ORDER BY n DESC")
        return {row["source"]: row["n"] for row in rows}

    def count_messages_by_status(self) -> dict[str, int]:
        """Count the archive per status, largest first; a status with no messages is present with 0."""
        rows = self.db.conn.execute("SELECT status, COUNT(*) AS n FROM messages GROUP BY status ORDER BY n DESC")
        counts = {row["status"]: row["n"] for row in rows}
        return counts | {status.value: 0 for status in MessageStatus if status.value not in counts}

    def _import_source(self, source: Source) -> int:
        """Run one source and insert what it found, skipping the source keys already archived; returns rows inserted."""
        settings = self.core.services.setting.get_settings()
        match source:
            case Source.CLAUDE_CODE:
                messages = claude_code.collect_messages(settings.claude_projects_dir)
            case Source.CODEX:
                messages = codex.collect_messages(settings.codex_sessions_dir)
            case _:
                assert_never(source)
        rows = [(m.source, m.source_key, m.typed_at.isoformat(), m.text, json.dumps(m.meta)) for m in messages]
        with self.db.transaction() as conn:
            cursor = conn.executemany(
                "INSERT OR IGNORE INTO messages (source, source_key, typed_at, text, meta) VALUES (?,?,?,?,?)", rows
            )
            return cursor.rowcount

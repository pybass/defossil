"""SQLite storage: the connection and the migration runner.

It runs no query of its own — a table's SQL belongs to its feature, and the schema lives in migrations.py.
"""

import sqlite3
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from defossil.core.migrations import MIGRATIONS


class Db:
    """The one connection every feature service runs its own SQL on."""

    def __init__(self, path: Path) -> None:
        """Open the database and apply pending migrations; the directory must exist (Core makes it)."""
        # autocommit=True gives every lone statement its own transaction, so a write is never left hanging in an
        # implicit one. check_same_thread=False because fastapi serves sync handlers from a threadpool, so a request
        # is not handled by the thread that opened this connection.
        self.conn = sqlite3.connect(path, autocommit=True, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._migrate()
        self._write_lock = threading.Lock()

    def close(self) -> None:
        """Close the connection."""
        self.conn.close()

    def _migrate(self) -> None:
        """Apply pending migrations, tracked via PRAGMA user_version."""
        version = int(self.conn.execute("PRAGMA user_version").fetchone()[0])
        for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
            # The script and the version bump commit together: a crash mid-migration rolls back cleanly, so a
            # migration is either fully applied and recorded, or not at all.
            self.conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {number};\nCOMMIT;")

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection]:
        """Run one or more statements as a single transaction; every write goes through here, reads need not.

        One at a time, under the lock: the connection is shared by the request threadpool, the background workers and
        the explain pool, so a lone autocommit write could join another thread's open transaction and be rolled
        back with it, and overlapping transactions would nest and commit each other's rows.
        """
        with self._write_lock:
            self.conn.execute("BEGIN")
            try:
                yield self.conn
            except Exception:
                self.conn.execute("ROLLBACK")
                raise
            self.conn.execute("COMMIT")

import sqlite3

from defossil.core.db.migrations import MIGRATIONS
from defossil.core.db.schema import SCHEMA


def _structure(script: str) -> dict[str, dict[str, object]]:
    """What *script* makes in an empty database, by table; the order of columns and the text of the SQL are left out."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(script)
    tables = conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'").fetchall()
    structure: dict[str, dict[str, object]] = {}
    for table, sql in tables:
        indexes = conn.execute(f"PRAGMA index_list('{table}')").fetchall()
        structure[table] = {
            # name: type, not null, default, position in the primary key
            "columns": {row[1]: row[2:] for row in conn.execute(f"PRAGMA table_info('{table}')")},
            # referenced table, column, referenced column, on update, on delete, match
            "foreign_keys": sorted(row[2:] for row in conn.execute(f"PRAGMA foreign_key_list('{table}')")),
            # name: unique, columns
            "indexes": {
                row[1]: (row[2], [column[2] for column in conn.execute(f"PRAGMA index_info('{row[1]}')")]) for row in indexes
            },
            "autoincrement": "AUTOINCREMENT" in sql.upper(),
        }
    conn.close()
    return structure


class TestSchema:
    def test_matches_migrations(self) -> None:
        """A new database, made from the full schema, has the same structure as one brought up by every migration."""
        assert _structure(SCHEMA) == _structure("\n".join(MIGRATIONS))

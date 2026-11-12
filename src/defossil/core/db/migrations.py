"""The migrations that bring an older database to the full schema in schema.py.

This file is append-only. A released migration never changes: databases already ran it. A mistake is fixed by a new
migration. A migration that changes the schema comes with the same change to schema.py, which new databases are made
from; one that only changes rows does not.
"""

# v1: the starting schema, which every later migration changes. The app never runs it: a new database is made from the
# full schema at the latest version.
_MIGRATION_V1 = """
CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    source_key TEXT NOT NULL,
    typed_at TEXT NOT NULL,
    text TEXT NOT NULL,
    meta TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    UNIQUE (source, source_key)
);
CREATE INDEX idx_messages_status ON messages (status);
CREATE INDEX idx_messages_typed_at ON messages (typed_at);
CREATE TABLE corrections (
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES messages(id),
    category TEXT NOT NULL,
    original TEXT NOT NULL,
    corrected TEXT NOT NULL,
    explanation TEXT NOT NULL,
    extra_explanation TEXT,
    acknowledged INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_corrections_message_id ON corrections (message_id);
CREATE TABLE reports (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    first_message_id INTEGER NOT NULL,
    last_message_id INTEGER NOT NULL,
    first_correction_id INTEGER NOT NULL,
    last_correction_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE ai_calls (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    category TEXT NOT NULL,
    backend TEXT NOT NULL,
    model TEXT,
    effort TEXT,
    prompt TEXT NOT NULL,
    reply TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    duration_ms INTEGER NOT NULL,
    error TEXT
);
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# v2: the chat under a report. `report_chat_turns` is the record of the conversation; `report_chat_sessions`
# holds the one CLI session the next turn resumes, replaced whenever a resume fails and the turns are replayed into
# a fresh session. `ai_calls.session_id` ties each chat call to the session it ran in; NULL for every other prompt.
_MIGRATION_V2 = """
CREATE TABLE report_chat_sessions (
    report_id INTEGER PRIMARY KEY REFERENCES reports(id),
    backend TEXT NOT NULL,
    session_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE report_chat_turns (
    id INTEGER PRIMARY KEY,
    report_id INTEGER NOT NULL REFERENCES reports(id),
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_report_chat_turns_report_id ON report_chat_turns (report_id);
ALTER TABLE ai_calls ADD COLUMN session_id TEXT;
"""

MIGRATIONS = (_MIGRATION_V1, _MIGRATION_V2)  # index+1 = PRAGMA user_version

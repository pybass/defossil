"""The full current schema, written as if the database were made from scratch.

A new database is made from SCHEMA at the latest version. Every schema change edits it and adds a migration that brings
the older databases to the same result. A migrated database may differ in the order of columns, never in their names,
types, or defaults. The meaning of each column is on its feature's model.
"""

# Times are ISO 8601 text. A flag is 0 or 1.
SCHEMA = """
-- The archive: every message the user typed, verbatim.
CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    source_key TEXT NOT NULL,
    typed_at TEXT NOT NULL,
    text TEXT NOT NULL,
    -- JSON object.
    meta TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    -- How a second collect run skips a message it already stored.
    UNIQUE (source, source_key)
);
CREATE INDEX idx_messages_status ON messages (status);
CREATE INDEX idx_messages_typed_at ON messages (typed_at);

-- What the reviewer found in the archive. Append-only, so an id is stable and a report can hold a range of them.
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

-- The lessons written over corrections. Append-only; the id ranges are inclusive and stay true forever.
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

-- The one CLI session the next chat turn of a report resumes. Replaced when a resume fails and the turns are replayed
-- into a new session.
CREATE TABLE report_chat_sessions (
    report_id INTEGER PRIMARY KEY REFERENCES reports(id),
    backend TEXT NOT NULL,
    session_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- The chat under a report: one row per question and its answer.
CREATE TABLE report_chat_turns (
    id INTEGER PRIMARY KEY,
    report_id INTEGER NOT NULL REFERENCES reports(id),
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_report_chat_turns_report_id ON report_chat_turns (report_id);

-- The cost log: every backend call, failed ones too.
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
    error TEXT,
    -- The CLI session a chat call ran in; NULL for every other prompt.
    session_id TEXT
);

-- The tunables the dashboard edits. A missing key means the default.
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

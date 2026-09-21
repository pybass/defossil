"""Database schema migrations, applied in order by Db."""

# Schema v1: the whole schema as it stood when migrations were introduced. `messages` is the archive, `corrections` is
# what the reviewer found in it, `reports` is the lessons written over both, `ai_calls` is the cost log of every
# backend call, and `settings` is the tunables the dashboard edits. `corrections` and `reports` are append-only, so
# their ids are stable and a report's stored id ranges stay true forever.
# IF NOT EXISTS on purpose: databases from before migrations sit at user_version 0 with these tables already present,
# so v1 adopts them instead of failing. Later migrations don't need it.
_MIGRATION_V1 = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    source_key TEXT NOT NULL,
    typed_at TEXT NOT NULL,
    text TEXT NOT NULL,
    meta TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    UNIQUE (source, source_key)
);
CREATE INDEX IF NOT EXISTS idx_messages_status ON messages (status);
CREATE INDEX IF NOT EXISTS idx_messages_typed_at ON messages (typed_at);
CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES messages(id),
    category TEXT NOT NULL,
    original TEXT NOT NULL,
    corrected TEXT NOT NULL,
    explanation TEXT NOT NULL,
    extra_explanation TEXT,
    acknowledged INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_corrections_message_id ON corrections (message_id);
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    first_message_id INTEGER NOT NULL,
    last_message_id INTEGER NOT NULL,
    first_correction_id INTEGER NOT NULL,
    last_correction_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ai_calls (
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
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Schema v2: the chat under a report. `report_chat_turns` is the record of the conversation; `report_chat_sessions`
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

MIGRATIONS = (_MIGRATION_V1, _MIGRATION_V2)  # append-only; index+1 = PRAGMA user_version

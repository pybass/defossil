"""The archive record and the small results the message service hands back."""

import json
import sqlite3
from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field


class Source(StrEnum):
    """Where a message came from; every member has a module under sources/ and is collected each sweep."""

    CLAUDE_CODE = "claude-code"  # Claude Code CLI transcripts
    CODEX = "codex"  # Codex CLI rollout files


class MessageStatus(StrEnum):
    """Where a message stands in the pipeline; it moves forward once per step, never backward."""

    NEW = "new"  # collected, not yet classified
    PENDING = "pending"  # classify passed it; waiting for a review batch
    REVIEWED = "reviewed"  # a review batch covered it — terminal
    NON_ENGLISH = "non-english"  # classify rejections — terminal
    TOO_SHORT = "too-short"
    NO_PROSE = "no-prose"
    TOO_LONG = "too-long"


class NewMessage(BaseModel):
    """What a source found, before the archive gives it an id."""

    source: Source
    source_key: str  # the source's own name for this message; unique within the source, and how a re-run skips it
    typed_at: datetime  # as the source reports it — not when it was archived
    text: str  # verbatim; immutable once stored
    meta: dict[str, str] = Field(default_factory=dict)  # provenance; the keys are the source module's own, display-only


class Message(NewMessage):
    """Something the user typed, exactly as found in a source — the archive, and what the reviewer sends verbatim."""

    id: int  # the SQLite rowid; not stable — clearing the archive and collecting again renumbers everything
    status: MessageStatus

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Self:
        """Rebuild a message from its row in the `messages` table."""
        return cls(
            id=row["id"],
            source=Source(row["source"]),
            source_key=row["source_key"],
            typed_at=datetime.fromisoformat(row["typed_at"]),
            text=row["text"],
            meta=json.loads(row["meta"]),
            status=MessageStatus(row["status"]),
        )

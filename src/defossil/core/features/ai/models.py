"""The AI call record: every backend call with what it cost."""

import sqlite3
from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel


class PromptCategory(StrEnum):
    """Which prompt a call sends; each category has its own model and effort settings."""

    REVIEW = "review"
    EXPLAIN = "explain"
    REPORT = "report"


class AiResponse(BaseModel):
    """What a backend returns for one prompt: the answer text, plus whatever metadata its CLI reports."""

    reply: str
    model: str | None = None  # as the CLI reported it; None when it does not say
    input_tokens: int | None = None  # cache reads and writes included — they are still input paid for
    output_tokens: int | None = None
    cost_usd: float | None = None


class AiCall(BaseModel):
    """One stored backend call; what the ai calls page lists."""

    id: int
    created_at: datetime
    category: PromptCategory
    backend: str  # the ai_backend setting at call time
    model: str | None  # as the CLI reported it, or the requested one when it does not say
    effort: str | None  # requested reasoning effort
    prompt: str  # full text sent, template plus appended data
    reply: str | None  # raw answer text; None when the call failed
    input_tokens: int | None  # None when the backend reports no usage (codex)
    output_tokens: int | None
    cost_usd: float | None
    duration_ms: int
    error: str | None  # why the call failed; the row is still written

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Self:
        """Rebuild a call from its row in the `ai_calls` table."""
        return cls.model_validate(dict(row))

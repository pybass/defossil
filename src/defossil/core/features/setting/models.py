"""The settings record: one typed snapshot of the `settings` table; defaults live here, not in the database."""

import sqlite3
from pathlib import Path
from typing import Annotated, Self

from pydantic import BaseModel, Field


class Settings(BaseModel):
    """Every tunable setting; a key missing from the table means the field's default."""

    native_language: str = "Russian"  # by its English name; the review and report prompts name it
    ai_backend: str = "claude"  # which CLI answers the prompts: "claude" or "codex"
    # Model and effort per prompt category, passed to the backend CLI as-is.
    # Effort values: claude takes low/medium/high/xhigh/max; codex takes minimal/low/medium/high.
    review_model: Annotated[str, Field(min_length=1)] = "opus"
    review_effort: Annotated[str, Field(min_length=1)] = "high"
    explain_model: Annotated[str, Field(min_length=1)] = "opus"
    explain_effort: Annotated[str, Field(min_length=1)] = "low"
    report_model: Annotated[str, Field(min_length=1)] = "opus"
    report_effort: Annotated[str, Field(min_length=1)] = "xhigh"
    claude_projects_dir: Path = Path.home() / ".claude" / "projects"
    codex_sessions_dir: Path = Path.home() / ".codex" / "sessions"
    messages_per_review: Annotated[int, Field(ge=1)] = 30  # the review batch size; a review runs only on a full batch
    corrections_per_report: Annotated[int, Field(ge=1)] = 300  # the report window size; filling it makes a report
    page_size: Annotated[int, Field(ge=1)] = 300  # rows per page on the web list pages

    @classmethod
    def from_rows(cls, rows: list[sqlite3.Row]) -> Self:
        """Rebuild settings from stored rows; unknown keys are ignored, missing ones keep their defaults."""
        return cls.model_validate({row["key"]: row["value"] for row in rows if row["key"] in cls.model_fields})

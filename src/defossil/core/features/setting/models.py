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
    chat_model: Annotated[str, Field(min_length=1)] = "opus"
    chat_effort: Annotated[str, Field(min_length=1)] = "low"
    source_claude_code_enabled: bool = False  # off = stop collecting from Claude Code; the archive keeps what it has
    source_codex_enabled: bool = False  # off = stop collecting from Codex; the archive keeps what it has
    claude_projects_dir: Path = Path.home() / ".claude" / "projects"
    codex_sessions_dir: Path = Path.home() / ".codex" / "sessions"
    messages_per_review: Annotated[int, Field(ge=1)] = 30  # the review batch size; a review runs only on a full batch
    corrections_per_report: Annotated[int, Field(ge=1)] = 300  # the report window size; filling it makes a report
    # Report section sizes, stated in the report prompt as ceilings: items are the habits, patterns, or phrases a section
    # lists, examples the wrong -> right pairs under one of them. The model shows fewer when the data has fewer.
    report_shame_items: Annotated[int, Field(ge=1)] = 4
    report_shame_examples: Annotated[int, Field(ge=2)] = 2
    report_repeated_items: Annotated[int, Field(ge=1)] = 4
    report_repeated_examples: Annotated[int, Field(ge=2)] = 3
    report_grammar_examples: Annotated[int, Field(ge=2)] = 4
    report_native_items: Annotated[int, Field(ge=1)] = 6
    report_shorter_items: Annotated[int, Field(ge=1)] = 5
    report_focus_examples: Annotated[int, Field(ge=2)] = 3
    page_size: Annotated[int, Field(ge=1)] = 300  # rows per page on the web list pages

    @classmethod
    def from_rows(cls, rows: list[sqlite3.Row]) -> Self:
        """Rebuild settings from stored rows; unknown keys are ignored, missing ones keep their defaults."""
        return cls.model_validate({row["key"]: row["value"] for row in rows if row["key"] in cls.model_fields})

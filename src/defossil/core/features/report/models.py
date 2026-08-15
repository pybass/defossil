"""The report record: one stored lesson over a fixed window of corrections."""

import sqlite3
from datetime import datetime
from typing import Self

from pydantic import BaseModel


class ReportWindow(BaseModel):
    """The corrections one report covers — first and last, with the messages that carry them; all bounds inclusive."""

    first_correction_id: int
    last_correction_id: int
    first_message_id: int
    last_message_id: int


class Report(BaseModel):
    """One generated report; its windows are id ranges, permanently true because corrections are append-only."""

    id: int
    created_at: datetime
    first_message_id: int  # the message carrying the window's first correction; inclusive, like every bound here
    last_message_id: int  # the message carrying the window's last correction; the next window starts after it
    first_correction_id: int
    last_correction_id: int  # the range holds exactly corrections_per_report corrections
    text: str  # the markdown the LLM wrote, verbatim
    acknowledged: bool  # the user pressed "got it": the lesson is read

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Self:
        """Rebuild a report from its row in the `reports` table."""
        return cls(
            id=row["id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            first_message_id=row["first_message_id"],
            last_message_id=row["last_message_id"],
            first_correction_id=row["first_correction_id"],
            last_correction_id=row["last_correction_id"],
            text=row["text"],
            acknowledged=bool(row["acknowledged"]),
        )

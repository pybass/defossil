"""Making the report: assembling its window's inputs, asking the backend for the lesson, and owning the stored history."""

from datetime import UTC, datetime

from defossil.core.errors import InvalidOperationError, NotFoundError
from defossil.core.features.correction.models import CorrectionCategory
from defossil.core.features.report.models import Report
from defossil.core.service import Service

# The whole input must stay under ~20-30k words: repeat-counting degrades long before the model's context limit.
# Corrections need no cap — the window is a
# fixed count — but a window's message span is unbounded (clean stretches add messages without corrections).
# Clean messages are input on purpose: the report judges style the review never flagged, message-wide and across
# messages, which no correction fragment can show.
MESSAGES_CAP = 300
PREVIOUS_REPORTS = 2


class ReportService(Service):
    """Owner of the `reports` table; only the pipeline thread makes a report, and one is never regenerated."""

    def count_unreported_corrections(self) -> int:
        """Count the corrections past the last report's window — filling `corrections_per_report` triggers the next one."""
        last = next(iter(self.get_last_reports(1)), None)
        return self.core.services.correction.count_corrections_after(last.last_correction_id if last else 0)

    def make_report(self) -> Report:
        """Ask the LLM for a lesson over the next `corrections_per_report` corrections, store it, and return it.

        The window starts after the previous report's ranges and closes on the correction that fills the count;
        raises when fewer have accumulated. A failed backend call raises and stores nothing.
        """
        count = self.core.services.setting.get_settings().corrections_per_report
        previous = self.get_last_reports(PREVIOUS_REPORTS)
        window = self.core.services.correction.find_report_window(previous[0].last_correction_id if previous else 0, count)
        if window is None:
            raise InvalidOperationError(f"Fewer than {count} corrections since the last report")
        corrections = self.core.services.correction.get_corrections_in_id_range(
            window.first_correction_id, window.last_correction_id
        )
        # Two tiers under the cap: every correction-carrying message (always inside the range — the range is defined
        # by them), then the newest clean messages as filler. The cap may only trim clean filler, never a message the
        # corrections quote.
        carrying_ids = {c.message_id for c in corrections}
        carrying = self.core.services.message.get_messages_by_ids(sorted(carrying_ids))
        recent = self.core.services.message.get_reviewed_messages_in_range(
            window.first_message_id, window.last_message_id, MESSAGES_CAP
        )
        clean = [m for m in recent if m.id not in carrying_ids]
        fill_count = max(0, MESSAGES_CAP - len(carrying))
        messages = sorted(carrying + clean[max(0, len(clean) - fill_count) :], key=lambda m: m.id)
        previous_corrections = (
            self.core.services.correction.get_corrections_in_id_range(
                previous[0].first_correction_id, previous[0].last_correction_id
            )
            if previous
            else []
        )
        # Typos are one-off slips by definition and the report ignores one-offs, so they never make the input.
        text = self.core.services.ai.send_report_prompt(
            corrections=[c for c in corrections if c.category != CorrectionCategory.TYPO],
            previous_corrections=[c for c in previous_corrections if c.category != CorrectionCategory.TYPO],
            messages=[m.text for m in messages],
            previous_reports=[r.text for r in previous],
        )
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO reports (created_at, first_message_id, last_message_id, first_correction_id, last_correction_id, text)
                VALUES (?,?,?,?,?,?)
                """,
                (
                    datetime.now(UTC).isoformat(),
                    window.first_message_id,
                    window.last_message_id,
                    window.first_correction_id,
                    window.last_correction_id,
                    text,
                ),
            )
        return self.get_last_reports(1)[0]

    def get_last_reports(self, n: int | None = None) -> list[Report]:
        """Return the reports newest first — all of them, or only *n* when given."""
        rows = self.db.conn.execute("SELECT * FROM reports ORDER BY id DESC LIMIT IFNULL(?, -1)", (n,))
        return [Report.from_row(row) for row in rows]

    def count_unacknowledged_reports(self) -> int:
        """Count the reports the user has not acknowledged yet — the nav's unread badge."""
        row = self.db.conn.execute("SELECT COUNT(*) FROM reports WHERE acknowledged = 0").fetchone()
        return int(row[0])

    def acknowledge_report(self, report_id: int) -> None:
        """Set the acknowledged flag: the user has read the lesson."""
        with self.db.transaction() as conn:
            if conn.execute("UPDATE reports SET acknowledged = 1 WHERE id = ?", (report_id,)).rowcount == 0:
                raise NotFoundError(f"No report {report_id}")

    def unacknowledge_report(self, report_id: int) -> None:
        """Take the acknowledged flag back off — the undo for a mis-click."""
        with self.db.transaction() as conn:
            if conn.execute("UPDATE reports SET acknowledged = 0 WHERE id = ?", (report_id,)).rowcount == 0:
                raise NotFoundError(f"No report {report_id}")

    def delete_last_report(self) -> int:
        """Delete the newest report and return its id — the one developer exception to the append-only rule.

        Reopens the report's correction window, so the pipeline rebuilds it on a later sweep — how a prompt change
        is tried on the same data.
        """
        last = next(iter(self.get_last_reports(1)), None)
        if last is None:
            raise NotFoundError("No reports")
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM reports WHERE id = ?", (last.id,))
        return last.id

    def get_report(self, report_id: int) -> Report:
        """Return one stored report."""
        row = self.db.conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"No report {report_id}")
        return Report.from_row(row)

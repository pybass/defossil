"""The Jinja environment the page routers render through."""

import re
from datetime import UTC, date, datetime
from difflib import SequenceMatcher
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from defossil.core.features.message.models import MessageStatus

if TYPE_CHECKING:
    from defossil.core.core import Core

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")  # Autoescaping is on by default


def _format_dt(moment: datetime) -> str:
    """Render a timestamp in local time as 'YYYY-MM-DD HH:MM:SS'."""
    return moment.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _format_hm(moment: datetime) -> str:
    """Render a timestamp in local time as 'HH:MM' — list pages carry the day in separator rows."""
    return moment.astimezone().strftime("%H:%M")


def _local_date(moment: datetime) -> date:
    """Reduce a timestamp to its local calendar day, for grouping rows under date separators."""
    return moment.astimezone().date()


def _format_duration(seconds: float) -> str:
    """Render a duration at one coarse unit: seconds under 90 s, then minutes, hours, days."""
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 90 * 60:
        return f"{seconds / 60:.0f} min"
    if seconds < 36 * 3600:
        return f"{seconds / 3600:.0f} h"
    return f"{seconds / 86400:.0f} d"


def _format_ago(moment: datetime) -> str:
    """Render how long ago a moment was, as coarsely as `dur`."""
    return _format_duration((datetime.now(UTC) - moment).total_seconds()) + " ago"


def _word_diff(original: str, correction: str) -> Markup:
    """Merge a correction pair into one phrase where only the changed words carry <del>/<ins>."""
    a, b = original.split(), correction.split()
    parts: list[Markup] = []
    for op, a1, a2, b1, b2 in SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            parts.append(escape(" ".join(a[a1:a2])))
        else:
            if a1 < a2:
                parts.append(Markup("<del>{}</del>").format(" ".join(a[a1:a2])))
            if b1 < b2:
                parts.append(Markup("<ins>{}</ins>").format(" ".join(b[b1:b2])))
    return Markup(" ").join(parts)


# No DOTALL: pairs are one line by prompt contract, and a malformed pair must not swallow lines up to the next one.
_CORRECTION_RE = re.compile(r"<wrong>(.*?)</wrong>\s*<right>(.*?)</right>(?:\s*<why>(.*?)</why>)?")


def _correction_tags(text: str) -> str:
    """Replace <wrong>/<right>[<why>] pairs in report markdown with the collapsed word-diff widget the corrections page uses."""

    def widget(m: re.Match[str]) -> str:
        """One pair as a details widget, on one line — a line break would make marked close the surrounding list item."""
        wrong, right = m[1].strip(), m[2].strip()
        why = f' <span class="diff-why">{escape(m[3].strip())}</span>' if m[3] else ""
        return (
            f'<details class="diff"><summary>{_word_diff(wrong, right)}</summary>'
            f'<div class="was">{escape(wrong)}</div><div class="now">{escape(right)}</div></details>{why}'
        )

    # A pair alone on its line gets a block wrapper — consecutive bare pairs would otherwise flow side by side,
    # because marked passes adjacent raw-HTML lines through as one block and the widgets are inline-block.
    # The trailing newline blank-line-terminates the HTML block, so the prose after it is parsed as markdown again.
    full_line = re.compile(rf"^{_CORRECTION_RE.pattern}[ \t]*$", re.MULTILINE)
    text = full_line.sub(lambda m: f'<div class="diff-item">{widget(m)}</div>\n', text)
    return _CORRECTION_RE.sub(widget, text)


def configure_jinja(core: Core) -> None:
    """Register the filters and the globals bound to the live *core*; called once, before anything renders."""

    def due_work() -> dict[str, int] | None:
        """Return the counts a worker run is waiting on while auto AI is off; None when nothing is due — the header icon's data.

        A count is included only past its threshold: below a full batch or window, turning auto AI on would do nothing,
        and the icon must never nudge a no-op.
        """
        if core.services.ai.is_auto_ai():
            return None
        settings = core.services.setting.get_settings()
        due = {}
        pending = core.services.message.count_messages(MessageStatus.PENDING)
        if pending >= settings.messages_per_review:
            due["pending"] = pending
        unreported = core.services.report.count_unreported_corrections()
        if unreported >= settings.corrections_per_report:
            due["unreported"] = unreported
        return due or None

    templates.env.filters["dt"] = _format_dt
    templates.env.filters["dur"] = _format_duration
    templates.env.filters["ago"] = _format_ago
    templates.env.filters["hm"] = _format_hm
    templates.env.filters["day"] = _local_date
    templates.env.filters["worddiff"] = _word_diff
    templates.env.filters["correctiontags"] = _correction_tags
    templates.env.globals["version"] = version("defossil")
    templates.env.globals["auto_ai"] = core.services.ai.is_auto_ai
    templates.env.globals["unread_reports"] = core.services.report.count_unacknowledged_reports
    templates.env.globals["due_work"] = due_work

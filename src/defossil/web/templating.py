"""The Jinja environment the page routers render through."""

from datetime import UTC, date, datetime
from difflib import SequenceMatcher
from importlib.metadata import version
from pathlib import Path

from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

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


templates.env.filters["dt"] = _format_dt
templates.env.filters["dur"] = _format_duration
templates.env.filters["ago"] = _format_ago
templates.env.filters["hm"] = _format_hm
templates.env.filters["day"] = _local_date
templates.env.filters["worddiff"] = _word_diff
templates.env.globals["version"] = version("defossil")

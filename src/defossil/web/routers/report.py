"""The reports: every stored lesson, how far the next one is, and the chat under each; also owns the root redirect."""

from pathlib import Path
from typing import Annotated, Self

from fastapi import APIRouter
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from defossil.core.core import Core
from defossil.core.features.message.models import MessageStatus
from defossil.core.features.report.models import ReportChatTurn
from defossil.web.templating import correction_tags, format_dt, templates


class ChatRequest(BaseModel):
    """The body of the chat ask."""

    question: Annotated[str, Field(max_length=4000)]  # a chat line, not a document; blank is refused by the service


class ChatTurnView(BaseModel):
    """One turn as the page renders it: the answer already carries the correction widgets, the rest is markdown."""

    id: int
    question: str
    answer: str  # markdown with <wrong>/<right> pairs replaced by the diff widget; sanitized on the page
    created_at: str  # local time, as the page shows every timestamp

    @classmethod
    def from_turn(cls, turn: ReportChatTurn) -> Self:
        return cls(id=turn.id, question=turn.question, answer=correction_tags(turn.answer), created_at=format_dt(turn.created_at))


class ChatStatus(BaseModel):
    """One report's chat state, as the page's poll sees it."""

    pending: bool  # a turn is queued or being asked right now
    turns: list[ChatTurnView]  # the turns past the poll's cursor, in order


def create_router(core: Core) -> APIRouter:
    router = APIRouter()

    @router.get("/")
    def index() -> RedirectResponse:
        """Redirect to the corrections — the dashboard has no overview page."""
        return RedirectResponse("/corrections")

    @router.get("/reports", response_class=HTMLResponse)
    def report_list(request: Request) -> HTMLResponse:
        """Every stored report, newest first, and how many corrections the next window already holds."""
        reports = core.services.report.get_last_reports()
        ctx = {
            "reports": reports,
            # Report id -> reviewed messages its window spans; corrections / this is the improvement-trend rate,
            # falling as clean messages between mistakes get more frequent.
            "reviewed_counts": {
                r.id: core.services.message.count_reviewed_messages_in_range(r.first_message_id, r.last_message_id)
                for r in reports
            },
            "unreported": core.services.report.count_unreported_corrections(),
            "window": core.services.setting.get_settings().corrections_per_report,
            "pending": core.services.message.count_messages(MessageStatus.PENDING),
        }
        return templates.TemplateResponse(request, "reports.html", ctx)

    @router.get("/reports/{report_id}", response_class=HTMLResponse)
    def report_page(request: Request, report_id: int) -> HTMLResponse:
        """One report with its chat; the markdown of both is rendered in the browser."""
        ctx = {
            "r": core.services.report.get_report(report_id),
            # Plain dicts: the page embeds them with Jinja's tojson, which cannot serialize a model.
            "turns": [ChatTurnView.from_turn(t).model_dump() for t in core.services.report.get_chat_turns(report_id)],
            "chat_pending": report_id in core.services.report.get_pending_chat_ids(),
        }
        return templates.TemplateResponse(request, "report.html", ctx)

    @router.post("/reports/{report_id}/chat")
    def chat_ask(report_id: int, body: ChatRequest) -> dict[str, bool]:
        """Queue the question for the report's chat and return at once; the page polls for the answer.

        409 while the report already has a turn in flight: the page keeps the question for a retry.
        """
        core.services.report.ask(report_id, body.question)
        return {"pending": True}

    @router.get("/reports/{report_id}/chat-status")
    def chat_status(report_id: int, after_id: int = 0) -> ChatStatus:
        """One poll: whether a turn is in flight, and the turns past *after_id* — the last one the page has."""
        return ChatStatus(
            pending=report_id in core.services.report.get_pending_chat_ids(),
            turns=[ChatTurnView.from_turn(t) for t in core.services.report.get_chat_turns(report_id, after_id)],
        )

    @router.get("/reports/{report_id}/download", response_class=HTMLResponse)
    def report_download(request: Request, report_id: int) -> HTMLResponse:
        """Serve the report as one self-contained HTML file — scripts and images inlined, so it can be shared."""
        static = Path(__file__).parent.parent / "static"
        ctx = {
            "r": core.services.report.get_report(report_id),
            "turns": [ChatTurnView.from_turn(t) for t in core.services.report.get_chat_turns(report_id)],
            "marked_js": (static / "marked.min.js").read_text(encoding="utf-8"),
            "purify_js": (static / "purify.min.js").read_text(encoding="utf-8"),
            "glossary_js": (static / "glossary.js").read_text(encoding="utf-8"),
            "logo_svg": (static / "logo.svg").read_text(encoding="utf-8"),
        }
        headers = {"Content-Disposition": f'attachment; filename="defossil-report-{report_id}.html"'}
        return templates.TemplateResponse(request, "report_export.html", ctx, headers=headers)

    @router.post("/reports/{report_id}/acknowledge")
    def acknowledge(report_id: int) -> dict[str, bool]:
        """Mark the report read."""
        core.services.report.acknowledge_report(report_id)
        return {"acknowledged": True}

    @router.post("/reports/{report_id}/unacknowledge")
    def unacknowledge(report_id: int) -> dict[str, bool]:
        """Undo a mis-clicked acknowledge."""
        core.services.report.unacknowledge_report(report_id)
        return {"acknowledged": False}

    return router

"""The correction pages: the flat correction list and the per-correction actions."""

from datetime import UTC, datetime

from fastapi import APIRouter
from fastapi.requests import Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from defossil.core.core import Core
from defossil.core.errors import InvalidOperationError
from defossil.core.features.correction.models import CATEGORIES, CorrectionCategory
from defossil.core.features.message.models import MessageStatus
from defossil.web.templating import templates


class ExplainRequest(BaseModel):
    """The body of the explain call."""

    question: str = ""  # the user's own question; empty means just the default ask


class ExplainStatus(BaseModel):
    """One correction's explain state, as the list page's poll sees it."""

    pending: bool  # queued or being asked right now
    extra_explanation: str | None  # the stored answer; still the old one (or None) while pending


def create_router(core: Core) -> APIRouter:
    """Build the router with *core* bound to its routes."""
    router = APIRouter()

    @router.get("/corrections", response_class=HTMLResponse)
    def correction_list(request: Request, category: str = "", show: str = "", page: int = 1) -> HTMLResponse:
        """Paginated corrections, newest message first; the default view hides acknowledged ones, show=all lifts that."""
        try:
            category_filter = CorrectionCategory(category) if category else None
        except ValueError as e:
            raise InvalidOperationError(str(e)) from e
        show = "all" if show == "all" else ""
        acknowledged = None if show == "all" else False
        page_size = core.services.setting.get_settings().page_size
        total = core.services.correction.count_corrections(category_filter, acknowledged)
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(max(page, 1), pages)
        offset = (page - 1) * page_size
        ctx = {
            "rows": core.services.correction.get_corrections(category_filter, acknowledged, page_size, offset),
            "total": total,
            "page": page,
            "pages": pages,
            "category": category,
            "show": show,
            "categories": core.services.correction.count_corrections_by_category(acknowledged),
            "catalog": CATEGORIES,
            "pending_explains": core.services.correction.get_pending_explanation_ids(),
            "pending": core.services.message.count_messages(MessageStatus.PENDING),
            "today": datetime.now(UTC).astimezone().date(),
        }
        return templates.TemplateResponse(request, "corrections.html", ctx)

    @router.post("/corrections/{correction_id}/acknowledge")
    def acknowledge(correction_id: int) -> dict[str, bool]:
        """Flag one correction as read and understood; the list page's ack button."""
        core.services.correction.acknowledge_correction(correction_id)
        return {"acknowledged": True}

    @router.post("/corrections/{correction_id}/unacknowledge")
    def unacknowledge(correction_id: int) -> dict[str, bool]:
        """Take the flag back off; the list page's undo button."""
        core.services.correction.unacknowledge_correction(correction_id)
        return {"acknowledged": False}

    @router.post("/corrections/{correction_id}/explain")
    def explain(correction_id: int, body: ExplainRequest) -> dict[str, bool]:
        """Queue the correction for the explain worker and return at once; the list page polls for the result."""
        core.services.correction.request_explanation(correction_id, body.question)
        return {"pending": True}

    @router.get("/corrections/explain-status")
    def explain_status(ids: str = "") -> dict[int, ExplainStatus]:
        """One poll over the given comma-separated ids: each one's queue state and stored explanation."""
        try:
            wanted = [int(raw) for raw in ids.split(",") if raw.strip()]
        except ValueError as e:
            raise InvalidOperationError(str(e)) from e
        pending = core.services.correction.get_pending_explanation_ids()
        stored = core.services.correction.get_extra_explanations(wanted)
        return {cid: ExplainStatus(pending=cid in pending, extra_explanation=stored.get(cid)) for cid in wanted}

    return router

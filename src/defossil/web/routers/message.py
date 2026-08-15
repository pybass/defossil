"""The archive pages: the message list and one message in full."""

from datetime import UTC, datetime

from fastapi import APIRouter
from fastapi.requests import Request
from fastapi.responses import HTMLResponse

from defossil.core.core import Core
from defossil.core.errors import InvalidOperationError
from defossil.core.features.message.models import MessageStatus, Source
from defossil.web.templating import templates


def create_router(core: Core) -> APIRouter:
    """Build the router with *core* bound to its routes."""
    router = APIRouter()

    @router.get("/messages", response_class=HTMLResponse)
    def message_list(request: Request, status: str = "reviewed", source: str = "", page: int = 1) -> HTMLResponse:
        """Paginated messages; the status filter defaults to reviewed and 'all' lifts it, blank source is no filter."""
        try:
            status_filter = None if status == "all" else MessageStatus(status)
            source_filter = Source(source) if source else None
        except ValueError as e:
            raise InvalidOperationError(str(e)) from e
        page_size = core.services.setting.get_settings().page_size
        total = core.services.message.count_messages(status_filter, source_filter)
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(max(page, 1), pages)
        rows = core.services.message.get_messages(status_filter, source_filter, page_size, (page - 1) * page_size)
        ctx = {
            "rows": rows,
            "correction_counts": core.services.correction.count_corrections_by_message([m.id for m in rows]),
            "total": total,
            "page": page,
            "pages": pages,
            "status": status,
            "source": source,
            "statuses": core.services.message.count_messages_by_status(),
            "sources": core.services.message.count_messages_by_source(),
            "today": datetime.now(UTC).astimezone().date(),
        }
        return templates.TemplateResponse(request, "messages.html", ctx)

    @router.get("/messages/{message_id}", response_class=HTMLResponse)
    def message_detail(request: Request, message_id: int) -> HTMLResponse:
        """One message in full, with its corrections when it was reviewed."""
        ctx = {
            "message": core.services.message.get_message(message_id),
            "corrections": core.services.correction.get_message_corrections(message_id),
        }
        return templates.TemplateResponse(request, "message.html", ctx)

    return router

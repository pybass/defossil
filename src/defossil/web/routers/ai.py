"""The AI call pages: the log of every backend call and what it cost, and one call in full."""

from datetime import UTC, datetime

from fastapi import APIRouter
from fastapi.requests import Request
from fastapi.responses import HTMLResponse

from defossil.core.core import Core
from defossil.core.errors import InvalidOperationError
from defossil.core.features.ai.models import PromptCategory
from defossil.web.templating import templates


def create_router(core: Core) -> APIRouter:
    """Build the router with *core* bound to its routes."""
    router = APIRouter()

    @router.get("/system/ai-calls", response_class=HTMLResponse)
    def call_list(request: Request, category: str = "", page: int = 1) -> HTMLResponse:
        """Paginated call log, newest first; a blank category means no filter."""
        try:
            category_filter = PromptCategory(category) if category else None
        except ValueError as e:
            raise InvalidOperationError(str(e)) from e
        page_size = core.services.setting.get_settings().page_size
        total = core.services.ai.count_calls(category_filter)
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(max(page, 1), pages)
        ctx = {
            "rows": core.services.ai.get_calls(category_filter, page_size, (page - 1) * page_size),
            "total": total,
            "page": page,
            "pages": pages,
            "category": category,
            "categories": core.services.ai.count_calls_by_category(),
            "today": datetime.now(UTC).astimezone().date(),
        }
        return templates.TemplateResponse(request, "ai_calls.html", ctx)

    @router.get("/system/ai-calls/{call_id}", response_class=HTMLResponse)
    def call_detail(request: Request, call_id: int) -> HTMLResponse:
        """One call in full: the prompt exactly as sent, and the raw reply."""
        return templates.TemplateResponse(request, "ai_call.html", {"call": core.services.ai.get_call(call_id)})

    return router

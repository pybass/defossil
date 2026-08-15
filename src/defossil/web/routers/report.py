"""The reports: every stored lesson and how far the next one is; also owns the root redirect."""

from fastapi import APIRouter
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from defossil.core.core import Core
from defossil.core.features.message.models import MessageStatus
from defossil.web.templating import templates


def create_router(core: Core) -> APIRouter:
    """Build the router with *core* bound to its routes."""
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
        """One report; its markdown is rendered in the browser."""
        return templates.TemplateResponse(request, "report.html", {"r": core.services.report.get_report(report_id)})

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

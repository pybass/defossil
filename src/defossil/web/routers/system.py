"""The system section: a redirect to its first tab, the prompts as sent, the auto-AI page, and developer tools."""

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import APIRouter
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from defossil.core.core import Core
from defossil.core.features.correction.service import REVIEW_INTERVAL
from defossil.core.features.message.models import MessageStatus
from defossil.core.features.report.service import REPORT_INTERVAL
from defossil.web.templating import templates


@dataclass(frozen=True)
class PromptView:
    """One prompt as the /system/prompts page shows it."""

    name: str  # the file under core/features/ai/prompts/
    text: str  # placeholders filled, exactly as sent
    appended: str  # what the caller appends after the prompt at call time


def create_router(core: Core) -> APIRouter:
    """Build the router with *core* bound to its routes."""
    router = APIRouter()

    @router.get("/system")
    def system_home() -> RedirectResponse:
        """Send the bare section URL to the first tab."""
        return RedirectResponse("/system/settings")

    @router.post("/system/auto-ai/toggle")
    def auto_ai_toggle() -> dict[str, bool]:
        """Flip auto AI on or off; the header toggle. Turning on wakes both workers so a run starts now."""
        enabled = not core.services.ai.is_auto_ai()
        core.services.ai.set_auto_ai(enabled)
        if enabled:
            core.services.correction.review_now()
            core.services.report.report_now()
        return {"enabled": enabled}

    @router.post("/system/auto-ai/run")
    def auto_ai_run() -> RedirectResponse:
        """Start both workers now instead of waiting out their intervals; the auto-AI page's button."""
        core.services.correction.review_now()
        core.services.report.report_now()
        return RedirectResponse("/system/auto-ai", status_code=303)

    @router.get("/system/prompts", response_class=HTMLResponse)
    def prompt_list(request: Request) -> HTMLResponse:
        """Every prompt the app sends, placeholders filled, exactly as the backend sees it."""
        ctx = {
            "prompts": [
                PromptView("review.md", core.services.ai.get_review_prompt(), "the batch of messages as JSON"),
                PromptView("explain.md", core.services.ai.get_explain_prompt(), "the correction and its message as JSON"),
                PromptView(
                    "report.md", core.services.ai.get_report_prompt(), "corrections, messages and previous reports as JSON"
                ),
            ],
        }
        return templates.TemplateResponse(request, "prompts.html", ctx)

    @router.get("/system/auto-ai", response_class=HTMLResponse)
    def auto_ai_page(request: Request) -> HTMLResponse:
        """Everything about auto AI: state, each worker's last run, and progress toward the next batch and report."""
        now = datetime.now(UTC)
        review = core.services.correction.get_review_status()
        report = core.services.report.get_report_status()
        settings = core.services.setting.get_settings()
        pending = core.services.message.count_messages(MessageStatus.PENDING)
        unreported = core.services.report.count_unreported_corrections()
        ctx = {
            "pending": pending,
            "batch_size": settings.messages_per_review,
            "review_pct": min(100, round(100 * pending / settings.messages_per_review)),
            "unreported": unreported,
            "window": settings.corrections_per_report,
            "report_pct": min(100, round(100 * unreported / settings.corrections_per_report)),
            "review": review,
            "report": report,
            "next_review_in": max(0.0, REVIEW_INTERVAL - (now - review.reviewed_at).total_seconds()) if review else None,
            "next_report_in": max(0.0, REPORT_INTERVAL - (now - report.reported_at).total_seconds()) if report else None,
        }
        return templates.TemplateResponse(request, "auto_ai.html", ctx)

    @router.get("/system/developer", response_class=HTMLResponse)
    def developer_page(request: Request) -> HTMLResponse:
        """Developer tools: actions that break the app's normal rules, for working on defossil itself."""
        last = next(iter(core.services.report.get_last_reports(1)), None)
        return templates.TemplateResponse(request, "developer.html", {"last": last})

    @router.post("/system/developer/delete-last-report")
    def delete_last_report() -> RedirectResponse:
        """Delete the newest report so the report worker rebuilds its window — for trying prompt changes."""
        core.services.report.delete_last_report()
        return RedirectResponse("/system/developer", status_code=303)

    return router

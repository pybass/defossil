"""The system section: a redirect to its first tab, the prompts as sent, the pipeline page, and developer tools."""

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import APIRouter
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from defossil.core.core import Core
from defossil.core.features.message.models import MessageStatus
from defossil.core.pipeline import SWEEP_INTERVAL
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

    @router.post("/system/pipeline/toggle")
    def pipeline_toggle() -> dict[str, bool]:
        """Flip the whole pipeline on or off; the header toggle."""
        enabled = not core.services.pipeline.is_enabled()
        core.services.pipeline.set_enabled(enabled)
        return {"enabled": enabled}

    @router.post("/system/pipeline/sweep")
    def pipeline_sweep() -> RedirectResponse:
        """Start a sweep now instead of waiting out the interval; the pipeline page's button."""
        core.services.pipeline.sweep_now()
        return RedirectResponse("/system/pipeline", status_code=303)

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

    @router.get("/system/pipeline", response_class=HTMLResponse)
    def pipeline_page(request: Request) -> HTMLResponse:
        """Everything about the pipeline: state, last sweep, and progress toward the next review batch and report."""
        now = datetime.now(UTC)
        pipeline = core.services.pipeline.get_status()
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
            "pipeline": pipeline,
            "sweep_interval": SWEEP_INTERVAL,
            "next_sweep_in": max(0.0, SWEEP_INTERVAL - (now - pipeline.swept_at).total_seconds()) if pipeline else None,
        }
        return templates.TemplateResponse(request, "pipeline.html", ctx)

    @router.get("/system/developer", response_class=HTMLResponse)
    def developer_page(request: Request) -> HTMLResponse:
        """Developer tools: actions that break the app's normal rules, for working on defossil itself."""
        last = next(iter(core.services.report.get_last_reports(1)), None)
        return templates.TemplateResponse(request, "developer.html", {"last": last})

    @router.post("/system/developer/delete-last-report")
    def delete_last_report() -> RedirectResponse:
        """Delete the newest report so the pipeline rebuilds its window — for trying prompt changes."""
        core.services.report.delete_last_report()
        return RedirectResponse("/system/developer", status_code=303)

    return router

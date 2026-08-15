"""The FastAPI application.

Routers stay thin adapters: they translate HTTP into Core calls and back, and hold no logic of their own.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.requests import Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from defossil.core.core import Core
from defossil.core.errors import InvalidOperationError, NotFoundError
from defossil.web.routers import ai, correction, message, report, setting, system
from defossil.web.templating import templates


def create_app(core: Core) -> FastAPI:
    """Build the application with *core* bound to its routes; serving is what runs the workers."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        """Hold the services running for exactly as long as the server is up."""
        core.start()
        yield
        core.stop()

    app = FastAPI(title="defossil", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    # The header is on every page, so its data (toggle state, unread count) comes from template globals, not per-route context.
    templates.env.globals["pipeline_enabled"] = core.services.pipeline.is_enabled
    templates.env.globals["unread_reports"] = core.services.report.count_unacknowledged_reports
    app.include_router(report.create_router(core))
    app.include_router(message.create_router(core))
    app.include_router(correction.create_router(core))
    app.include_router(ai.create_router(core))
    app.include_router(setting.create_router(core))
    app.include_router(system.create_router(core))

    @app.exception_handler(NotFoundError)
    def not_found(_request: Request, exc: Exception) -> PlainTextResponse:
        """Turn a missing record into a 404 so no route has to check for one."""
        return PlainTextResponse(str(exc), status_code=404)

    @app.exception_handler(InvalidOperationError)
    def invalid(_request: Request, exc: Exception) -> PlainTextResponse:
        """Turn a broken model rule into a 400, same reason."""
        return PlainTextResponse(str(exc), status_code=400)

    return app

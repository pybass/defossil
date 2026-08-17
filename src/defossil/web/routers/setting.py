"""The settings page: every stored setting in one form, saved as a whole."""

from urllib.parse import parse_qsl

from fastapi import APIRouter
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from defossil.core.core import Core
from defossil.core.errors import InvalidOperationError
from defossil.core.features.setting.models import Settings
from defossil.web.templating import templates


def create_router(core: Core) -> APIRouter:
    """Build the router with *core* bound to its routes."""
    router = APIRouter()

    @router.get("/system/settings", response_class=HTMLResponse)
    def settings_page(request: Request) -> HTMLResponse:
        """Show the form over the current values; a stored value and a default look the same."""
        context = {"settings": core.services.setting.get_settings(), "data_dir": core.data_dir}
        return templates.TemplateResponse(request, "settings.html", context)

    @router.post("/system/settings")
    async def settings_save(request: Request) -> RedirectResponse:
        """Replace the settings with the form and come back to it; an emptied field returns to its default."""
        # Parsed by hand: request.form() needs the python-multipart package even for a plain urlencoded body.
        form = dict(parse_qsl((await request.body()).decode()))
        try:
            settings = Settings.model_validate({key: value.strip() for key, value in form.items() if value.strip()})
        except ValidationError as e:
            raise InvalidOperationError(str(e)) from e
        core.services.setting.save_settings(settings)
        return RedirectResponse("/system/settings", status_code=303)

    return router

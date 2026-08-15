"""Composition root — the single object the CLI and the web app work through."""

from pathlib import Path

from defossil.core.db import Db
from defossil.core.features.ai.service import AiService
from defossil.core.features.correction.service import CorrectionService
from defossil.core.features.message.service import MessageService
from defossil.core.features.report.service import ReportService
from defossil.core.features.setting.service import SettingService
from defossil.core.pipeline import Pipeline
from defossil.core.service import Service


class Services:
    """Every feature service in one namespace, so `core.*` stays the storage and `core.services.*` the work.

    Plain fields, listed by hand: a service is added here and nowhere else. The pipeline sits last so it starts
    after every service it drives and stops before them.
    """

    def __init__(self, core: Core) -> None:
        """Build one service per feature over *core*. None of them may touch another while this runs."""
        self.setting = SettingService(core)
        self.ai = AiService(core)
        self.message = MessageService(core)
        self.correction = CorrectionService(core)
        self.report = ReportService(core)
        self.pipeline = Pipeline(core)
        # Read off the fields above, so a service is still added in one place.
        self._services: list[Service] = [value for value in vars(self).values() if isinstance(value, Service)]

    def start_all(self) -> None:
        """Start the services in the order they were built."""
        for service in self._services:
            service.on_start()

    def stop_all(self) -> None:
        """Stop them in the reverse order, so nothing is torn down under a service still using it."""
        for service in reversed(self._services):
            service.on_stop()


class Core:
    """A container, not a layer with behaviour: it owns the storage and the feature services over it.

    A client builds one Core and calls `core.services.correction.get_corrections(...)`; nothing here forwards that call.
    Core is also the lifecycle: `start` is what lets the pipeline run in the background, and only the server calls it.
    """

    # Where the data lives without --data-dir. A fixed path, never read from the environment:
    # a variable exported for some other tool must not move the archive.
    DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "defossil"

    def __init__(self, data_dir: Path) -> None:
        """Open the database under *data_dir* and build the services over it. Nothing runs yet."""
        data_dir.mkdir(parents=True, exist_ok=True)
        self.db = Db(data_dir / "defossil.db")
        self.services = Services(self)

    def start(self) -> None:
        """Start every service; nothing runs in the background before this."""
        self.services.start_all()

    def stop(self) -> None:
        """Stop the services, then close the database — in that order, or the pipeline outlives its connection."""
        self.services.stop_all()
        self.db.close()

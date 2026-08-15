"""The shape every feature service shares: a handle on Core, and through it on everything else."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # Core builds the services, so importing it for real here would close a cycle
    from defossil.core.core import Core
    from defossil.core.db import Db


class Service:
    """Base of every feature service: it reaches the storage and its neighbours through Core."""

    def __init__(self, core: Core) -> None:
        """Bind the service to the Core that built it."""
        self.core = core

    @property
    def db(self) -> Db:
        """The database, for the SQL of this feature's own table and no other."""
        return self.core.db

    def on_start(self) -> None:
        """Take whatever this service needs to run; called once, before the first request."""

    def on_stop(self) -> None:
        """Give it back; called once, while the database is still open."""

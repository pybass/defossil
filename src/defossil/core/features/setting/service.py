"""Reading and replacing the stored settings."""

from defossil.core.features.setting.models import Settings
from defossil.core.service import Service


class SettingService(Service):
    """Owner of the `settings` table; callers read per use and never cache, so an edit applies without a restart."""

    def get_settings(self) -> Settings:
        """Read the table into one typed snapshot."""
        return Settings.from_rows(self.db.conn.execute("SELECT key, value FROM settings").fetchall())

    def save_settings(self, settings: Settings) -> None:
        """Replace the stored rows, keeping only the fields that differ from their default — a default may change later."""
        defaults = Settings()
        changed = [name for name in Settings.model_fields if getattr(settings, name) != getattr(defaults, name)]
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM settings")
            conn.executemany(
                "INSERT INTO settings (key, value) VALUES (?, ?)", [(name, str(getattr(settings, name))) for name in changed]
            )

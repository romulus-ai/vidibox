"""Konfiguration ueber Umgebungsvariablen (Prefix VIDEOBOX_) bzw. .env-Datei."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VIDEOBOX_", env_file=".env", extra="ignore")

    data_dir: Path = Field(default=Path("./data"))
    admin_pin: str = Field(default="1234", min_length=4)
    max_volume: int = Field(default=70, ge=0, le=100)
    # Name des ALSA-Mixers (amixer scontrols), z.B. "Master" oder "Digital" (HiFiBerry DAC+)
    audio_control: str = Field(default="Master")
    max_resolution: int = Field(default=720, ge=240, le=2160)
    port: int = Field(default=8000)
    host: str = Field(default="0.0.0.0")
    # Sitzungsdauer fuer den Admin-Login in Stunden
    session_hours: int = Field(default=12)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "videobox.sqlite3"

    @property
    def videos_dir(self) -> Path:
        return self.data_dir / "videos"

    @property
    def thumbs_dir(self) -> Path:
        return self.data_dir / "thumbs"

    @property
    def tags_dir(self) -> Path:
        return self.data_dir / "tags"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.videos_dir, self.thumbs_dir, self.tags_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()

"""Operational configuration loaded from environment variables and portable root discovery."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def resolve_app_root() -> Path:
    """Resolve the application root directory across dev, installed, and portable `.exe` layouts.

    Resolution order:
    1. `IMOB_APP_ROOT` environment variable (set by the native `ImobPatoBranco.exe` launcher).
    2. Frozen executable parent directory (`sys.frozen` builds).
    3. Repository checkout root containing `alembic.ini` (`src/imob/config.py -> parents[2]`).
    4. Current working directory if it contains `alembic.ini`.

    Returns:
        Resolved `Path` to the application root directory.
    """
    env_root = os.environ.get("IMOB_APP_ROOT", "").strip()
    if env_root:
        return Path(env_root).expanduser().resolve()

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    source_root = Path(__file__).resolve().parents[2]
    if (source_root / "alembic.ini").exists():
        return source_root

    cwd = Path.cwd().resolve()
    if (cwd / "alembic.ini").exists():
        return cwd

    return source_root


class AppSettings(BaseSettings):
    """Immutable runtime settings for the Imob API, database, and sync scheduler."""

    model_config = SettingsConfigDict(
        env_prefix="IMOB_",
        frozen=True,
        extra="ignore",
    )

    db_path: Path = Field(default_factory=lambda: resolve_app_root() / "data" / "imob.db")
    target_city: str = Field(default="Pato Branco")
    target_state: str = Field(default="PR")
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8000, ge=1, le=65535)

    # Periodic & incremental sync timer configuration
    auto_sync_enabled: bool = Field(default=True)
    sync_interval_hours: float = Field(default=6.0, ge=0.25, le=168.0)
    timer_check_minutes: float = Field(default=15.0, ge=1.0, le=1440.0)

    # Polite crawling controls
    max_concurrent_agencies: int = Field(default=6, ge=1, le=20)
    per_domain_delay_seconds: float = Field(default=0.45, ge=0.0, le=10.0)
    http_timeout_seconds: float = Field(default=25.0, ge=5.0, le=120.0)
    max_pages_per_section: int = Field(default=60, ge=1, le=500)

    @property
    def sqlalchemy_url(self) -> str:
        """Return the synchronous SQLAlchemy SQLite connection URL (Windows & POSIX safe).

        Returns:
            SQLite connection URL string pointing to `db_path`.
        """
        return f"sqlite:///{self.db_path.resolve().as_posix()}"

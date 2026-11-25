"""Operational configuration loaded from environment variables and portable root discovery."""

from __future__ import annotations

import os
import sys
from datetime import datetime, time
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import URL

from imob.errors import ConfigurationError

_DEFAULT_SYNC_SCHEDULE: tuple[time, ...] = (time(6, 0), time(18, 0))


def parse_sync_schedule(raw: object) -> tuple[time, ...]:
    """Parse and validate a fixed daily sync schedule from a CSV string or sequence of `HH:MM` times.

    Args:
        raw: Comma-separated `HH:MM` string (e.g. `"06:00,18:00"`) or sequence of `time`/`str` entries.

    Returns:
        Deduplicated, chronologically sorted tuple of naive `datetime.time` objects.

    Raises:
        ValueError: When the schedule is empty or any entry is not a valid 24-hour `HH:MM` value.
    """
    entries: list[object]
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            raise ValueError("IMOB_SYNC_TIMES must contain at least one HH:MM time.")
        parts = [piece.strip() for piece in stripped.split(",")]
        if any(not piece for piece in parts):
            raise ValueError("IMOB_SYNC_TIMES must not contain empty comma-separated entries.")
        entries = list(parts)
    elif isinstance(raw, (list, tuple, set, frozenset)):
        entries = list(raw)
        if not entries:
            raise ValueError("IMOB_SYNC_TIMES must contain at least one HH:MM time.")
    else:
        raise ValueError("IMOB_SYNC_TIMES must be a comma-separated HH:MM string or sequence of times.")

    parsed: set[time] = set()
    for item in entries:
        if isinstance(item, time) and not isinstance(item, datetime):
            if item.tzinfo is not None or item.second != 0 or item.microsecond != 0:
                raise ValueError(f"Invalid sync time '{item}': expected naive HH:MM without seconds.")
            parsed.add(item)
            continue
        if isinstance(item, str):
            token = item.strip()
            if len(token) == 5 and token[2] == ":" and token[:2].isdigit() and token[3:].isdigit():
                hour = int(token[:2])
                minute = int(token[3:])
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    parsed.add(time(hour, minute))
                    continue
        raise ValueError(f"Invalid sync time '{item}': expected 24-hour HH:MM format (00:00..23:59).")

    return tuple(sorted(parsed))


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
    """Immutable runtime settings for the Imob API, MySQL database, and sync scheduler.

    The MySQL password is a `SecretStr`: it never appears in `repr`, logs, or error messages.
    """

    model_config = SettingsConfigDict(
        env_prefix="IMOB_",
        frozen=True,
        extra="ignore",
    )

    mysql_host: str = Field(default="127.0.0.1", min_length=1)
    mysql_port: int = Field(default=3306, ge=1, le=65535)
    mysql_user: str = Field(default="")
    mysql_password: SecretStr = Field(default=SecretStr(""))
    mysql_database: str = Field(default="imob", min_length=1, max_length=64)
    maps_dir: Path = Field(default_factory=lambda: resolve_app_root() / "data" / "maps")
    target_city: str = Field(default="Pato Branco")
    target_state: str = Field(default="PR")
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8000, ge=1, le=65535)

    # Fixed-time background sync scheduler & CLI TTL configuration
    auto_sync_enabled: bool = Field(default=True)
    sync_interval_hours: float = Field(default=6.0, ge=0.25, le=168.0)
    sync_schedule: Annotated[tuple[time, ...], NoDecode] = Field(
        default=_DEFAULT_SYNC_SCHEDULE,
        validation_alias=AliasChoices(
            "sync_schedule",
            "sync_times",
            "IMOB_SYNC_TIMES",
            "IMOB_SYNC_SCHEDULE",
        ),
    )
    sync_timezone: str = Field(default="America/Sao_Paulo")

    @field_validator("sync_schedule", mode="before")
    @classmethod
    def _validate_sync_schedule(cls, value: object) -> tuple[time, ...]:
        return parse_sync_schedule(value)

    @field_validator("sync_timezone")
    @classmethod
    def _validate_sync_timezone(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("IMOB_SYNC_TIMEZONE must not be empty.")
        try:
            ZoneInfo(cleaned)
        except (ZoneInfoNotFoundError, KeyError, ValueError) as exc:
            raise ValueError(f"Invalid IANA timezone '{cleaned}' in IMOB_SYNC_TIMEZONE.") from exc
        return cleaned

    # Polite crawling controls
    max_concurrent_agencies: int = Field(default=6, ge=1, le=20)
    per_domain_delay_seconds: float = Field(default=0.45, ge=0.0, le=10.0)
    http_timeout_seconds: float = Field(default=25.0, ge=5.0, le=120.0)
    max_pages_per_section: int = Field(default=60, ge=1, le=500)

    @property
    def database_url(self) -> URL:
        """Return the SQLAlchemy MySQL URL built from the individual `IMOB_MYSQL_*` settings.

        `URL.create` escapes special characters, and the `URL` object masks the password in
        `repr`/`str`, so the secret is never written to logs.

        Returns:
            `mysql+pymysql` URL using the `utf8mb4` connection charset.

        Raises:
            ConfigurationError: When the user or password is missing; names the variable, never a value.
        """
        missing = [
            name
            for name, value in (
                ("IMOB_MYSQL_USER", self.mysql_user.strip()),
                ("IMOB_MYSQL_PASSWORD", self.mysql_password.get_secret_value()),
            )
            if not value
        ]
        if missing:
            raise ConfigurationError(f"Missing required database setting(s): {', '.join(missing)}")
        return URL.create(
            "mysql+pymysql",
            username=self.mysql_user.strip(),
            password=self.mysql_password.get_secret_value(),
            host=self.mysql_host,
            port=self.mysql_port,
            database=self.mysql_database,
            query={"charset": "utf8mb4"},
        )

    @property
    def database_label(self) -> str:
        """Return a credential-free `host:port/database` description for logs and CLI output.

        Returns:
            Human-readable database location without user or password.
        """
        return f"{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"

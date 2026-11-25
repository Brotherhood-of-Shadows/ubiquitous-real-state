"""MySQL SQLAlchemy engine creation, session factory, health probe, and Alembic migrations."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from imob.config import AppSettings, resolve_app_root
from imob.errors import ConfigurationError, PersistenceError

from .locks import MIGRATION_LOCK_NAME, STARTUP_LOCK_TIMEOUT_SECONDS, MySqlNamedLock

_CONNECT_TIMEOUT_SECONDS = 10


def create_mysql_engine(settings: AppSettings) -> Engine:
    """Create a pooled MySQL `Engine` whose sessions read and write timestamps in UTC.

    Args:
        settings: Runtime settings holding the `IMOB_MYSQL_*` connection values.

    Returns:
        Configured SQLAlchemy `Engine` (`READ COMMITTED`, pre-ping, 30-minute recycle).

    Raises:
        ConfigurationError: When the user or password setting is missing.
    """
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_recycle=1800,
        isolation_level="READ COMMITTED",
        connect_args={
            "init_command": "SET time_zone='+00:00'",
            "connect_timeout": _CONNECT_TIMEOUT_SECONDS,
        },
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a thread-safe SQLAlchemy `sessionmaker` bound to `engine`.

    Args:
        engine: Target SQLAlchemy engine.

    Returns:
        Configured `sessionmaker` with `expire_on_commit=False`.
    """
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def ping_database(engine: Engine) -> bool:
    """Return whether the database answers `SELECT 1`.

    Args:
        engine: Target SQLAlchemy engine.

    Returns:
        `True` when the query succeeds, `False` on any database error.
    """
    try:
        with engine.connect() as conn:
            return bool(conn.scalar(text("SELECT 1")) == 1)
    except SQLAlchemyError:
        return False


def _migrations_dir() -> Path:
    migrations = resolve_app_root() / "migrations"
    if not (migrations / "env.py").is_file():
        raise ConfigurationError(
            f"Alembic migrations not found under {migrations}; set IMOB_APP_ROOT to the application root"
        )
    return migrations


def run_migrations(engine: Engine) -> None:
    """Apply the MySQL Alembic migrations up to `head` on `engine`.

    The connection is handed to Alembic through `Config.attributes`, so no URL or password is ever
    written into the Alembic configuration.

    Args:
        engine: Target SQLAlchemy engine.
    """
    migrations = _migrations_dir()
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(migrations).replace("%", "%%"))
    cfg.set_main_option("path_separator", "os")
    cfg.set_main_option("version_locations", str(migrations / "mysql_versions").replace("%", "%%"))
    lock = MySqlNamedLock(engine, MIGRATION_LOCK_NAME)
    if not lock.try_acquire(STARTUP_LOCK_TIMEOUT_SECONDS):
        raise PersistenceError("Timed out waiting for another IMOB process to finish database migrations.")
    try:
        with engine.begin() as connection:
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "head")
    finally:
        lock.release()

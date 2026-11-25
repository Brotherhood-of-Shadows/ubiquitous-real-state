"""SQLite SQLAlchemy engine creation, connection PRAGMAs, and FTS5 DDL helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from imob.config import resolve_app_root

FTS5_STATEMENTS: tuple[str, ...] = (
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS properties_fts USING fts5(
        title,
        neighborhood,
        street,
        building_name,
        ref_code,
        description,
        content='properties',
        content_rowid='id',
        tokenize='unicode61 remove_diacritics 2'
    );
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_properties_fts_ai AFTER INSERT ON properties BEGIN
        INSERT INTO properties_fts(
            rowid, title, neighborhood, street, building_name, ref_code, description
        )
        VALUES (
            new.id, coalesce(new.title, ''), coalesce(new.neighborhood, ''),
            coalesce(new.street, ''), coalesce(new.building_name, ''),
            coalesce(new.ref_code, ''), coalesce(new.description, '')
        );
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_properties_fts_ad AFTER DELETE ON properties BEGIN
        INSERT INTO properties_fts(
            properties_fts, rowid, title, neighborhood, street, building_name, ref_code, description
        )
        VALUES (
            'delete', old.id, coalesce(old.title, ''), coalesce(old.neighborhood, ''),
            coalesce(old.street, ''), coalesce(old.building_name, ''),
            coalesce(old.ref_code, ''), coalesce(old.description, '')
        );
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_properties_fts_au AFTER UPDATE ON properties BEGIN
        INSERT INTO properties_fts(
            properties_fts, rowid, title, neighborhood, street, building_name, ref_code, description
        )
        VALUES (
            'delete', old.id, coalesce(old.title, ''), coalesce(old.neighborhood, ''),
            coalesce(old.street, ''), coalesce(old.building_name, ''),
            coalesce(old.ref_code, ''), coalesce(old.description, '')
        );
        INSERT INTO properties_fts(
            rowid, title, neighborhood, street, building_name, ref_code, description
        )
        VALUES (
            new.id, coalesce(new.title, ''), coalesce(new.neighborhood, ''),
            coalesce(new.street, ''), coalesce(new.building_name, ''),
            coalesce(new.ref_code, ''), coalesce(new.description, '')
        );
    END;
    """,
)


def create_sqlite_engine(db_path: Path) -> Engine:
    """Create a configured SQLite `Engine` with WAL mode and foreign-key enforcement.

    Args:
        db_path: Filesystem path to the SQLite database file.

    Returns:
        Configured SQLAlchemy `Engine`.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    sqlite_url = f"sqlite:///{db_path.resolve().as_posix()}"
    engine = create_engine(
        sqlite_url,
        connect_args={"check_same_thread": False, "timeout": 15.0},
        pool_pre_ping=True,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute("PRAGMA synchronous = NORMAL")
            cursor.execute("PRAGMA busy_timeout = 10000")
        finally:
            cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a thread-safe SQLAlchemy `sessionmaker` bound to `engine`.

    Args:
        engine: Target SQLAlchemy engine.

    Returns:
        Configured `sessionmaker` with `expire_on_commit=False`.
    """
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def run_migrations(db_path: Path) -> None:
    """Apply Alembic schema migrations up to `head` for `db_path`.

    Args:
        db_path: Filesystem path to the target SQLite database.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    project_root = resolve_app_root()
    ini_path = project_root / "alembic.ini"
    sqlite_url = f"sqlite:///{db_path.resolve().as_posix()}".replace("%", "%%")
    cfg = AlembicConfig(str(ini_path))
    cfg.set_main_option("script_location", str(project_root / "migrations").replace("%", "%%"))
    cfg.set_main_option("sqlalchemy.url", sqlite_url)
    command.upgrade(cfg, "head")


def ensure_fts5_objects(engine: Engine) -> None:
    """Ensure SQLite FTS5 virtual table and sync triggers exist on `engine`.

    Args:
        engine: Target SQLAlchemy engine.
    """
    with engine.begin() as conn:
        for stmt in FTS5_STATEMENTS:
            conn.execute(text(stmt))

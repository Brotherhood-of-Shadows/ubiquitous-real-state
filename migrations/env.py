"""Alembic migration environment for the Imob MySQL database.

The application passes a live connection via `config.attributes["connection"]`
(see `imob.db.engine.run_migrations`), so no database URL or password lives in the
Alembic configuration. Standalone `alembic` CLI use must supply `sqlalchemy.url`
explicitly (for example `alembic -x` tooling or a private, uncommitted ini file).
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import Connection, engine_from_config, pool

from imob.db.orm import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in offline SQL-script mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _run_with_connection(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations on the application-provided connection, or on `sqlalchemy.url` as a fallback."""
    provided = config.attributes.get("connection")
    if isinstance(provided, Connection):
        _run_with_connection(provided)
        return
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _run_with_connection(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

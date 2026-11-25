"""Shared fixtures: a disposable MySQL database per test on a throwaway server.

Set `IMOB_TEST_MYSQL_URL` (for example `mysql+pymysql://root:<pw>@127.0.0.1:33306`, a root account on
the `compose.test.yaml` server only). Each test gets its own `imob_test_<hex>` database, migrated to
head, which is dropped afterwards. Without the URL, database tests are skipped; with
`IMOB_REQUIRE_MYSQL=1` they fail instead, so CI cannot pass by skipping.
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, make_url, pool, text
from sqlalchemy.engine import URL

from imob.config import AppSettings
from imob.db.engine import create_mysql_engine, run_migrations

_TEST_DB_PATTERN = re.compile(r"^imob_test_[0-9a-f]{16}$")
# Server flags the FULLTEXT behavior depends on (same as the production compose file).
_REQUIRED_SERVER_VARIABLES = {
    "innodb_ft_min_token_size": "2",
    "innodb_ft_enable_stopword": "OFF",
    "character_set_server": "utf8mb4",
    "collation_server": "utf8mb4_0900_ai_ci",
}


@dataclass(frozen=True)
class MySqlTestDatabase:
    """A migrated, per-test MySQL database and the settings/engine pointing at it."""

    settings: AppSettings
    engine: Engine
    name: str


def _admin_url() -> URL | None:
    raw = os.environ.get("IMOB_TEST_MYSQL_URL", "").strip()
    if not raw:
        if os.environ.get("IMOB_REQUIRE_MYSQL") == "1":
            pytest.fail("IMOB_REQUIRE_MYSQL=1 but IMOB_TEST_MYSQL_URL is not set")
        pytest.skip("MySQL test server not configured: set IMOB_TEST_MYSQL_URL (see compose.test.yaml)")
    url = make_url(raw)
    if not url.drivername.startswith("mysql"):
        pytest.fail("IMOB_TEST_MYSQL_URL must be a mysql+pymysql:// URL")
    return url.set(database=None)


@pytest.fixture(scope="session")
def mysql_admin_engine() -> Iterator[Engine]:
    """Server-level engine (no default database) on the throwaway test server."""
    url = _admin_url()
    assert url is not None
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT LOWER(VARIABLE_NAME), VARIABLE_VALUE FROM performance_schema.global_variables")
            ).all()
    except Exception as exc:  # Never echo the URL: it contains the password.
        pytest.fail(f"Cannot reach the MySQL test server: {type(exc).__name__}")
    actual = {str(name): str(value) for name, value in rows}
    wrong = {
        key: actual.get(key) for key, want in _REQUIRED_SERVER_VARIABLES.items() if actual.get(key) != want
    }
    if wrong:
        pytest.fail(f"MySQL test server flags differ from production (see compose.test.yaml): {wrong}")
    yield engine
    engine.dispose()


@pytest.fixture
def mysql_db(mysql_admin_engine: Engine, tmp_path: Path) -> Iterator[MySqlTestDatabase]:
    """Create `imob_test_<hex>`, migrate it to head, yield it, then drop only that database."""
    name = f"imob_test_{uuid.uuid4().hex[:16]}"
    assert _TEST_DB_PATTERN.fullmatch(name)
    with mysql_admin_engine.connect() as conn:
        conn.execute(text(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"))
    url = mysql_admin_engine.url
    settings = AppSettings(
        mysql_host=url.host or "127.0.0.1",
        mysql_port=url.port or 3306,
        mysql_user=url.username or "",
        mysql_password=SecretStr(url.password or ""),
        mysql_database=name,
        maps_dir=tmp_path / "maps",
        auto_sync_enabled=False,
    )
    engine = create_mysql_engine(settings)
    try:
        run_migrations(engine)
        yield MySqlTestDatabase(settings=settings, engine=engine, name=name)
    finally:
        engine.dispose()
        if _TEST_DB_PATTERN.fullmatch(name):
            with mysql_admin_engine.connect() as conn:
                conn.execute(text(f"DROP DATABASE IF EXISTS `{name}`"))

"""Unit tests for application root discovery, MySQL settings and secret handling, and CLI helpers."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, make_url

from imob.cli import _build_parser, _find_available_port, _run_serve
from imob.config import AppSettings, resolve_app_root
from imob.db.engine import create_mysql_engine, run_migrations
from imob.domain.enums import PlatformKind
from imob.domain.models import AgencyConfig, PropertyDetailPatch
from imob.errors import ConfigurationError, DomainValidationError
from imob.ports import ScrapeBatchResult
from imob.services.sync_service import SyncService, next_run_at


def test_resolve_app_root_honors_imob_app_root_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify `IMOB_APP_ROOT` overrides source-tree root discovery (container and packaged layouts)."""
    monkeypatch.setenv("IMOB_APP_ROOT", str(tmp_path))
    monkeypatch.delenv("IMOB_MAPS_DIR", raising=False)

    actual_root = resolve_app_root()
    settings = AppSettings()

    assert actual_root == tmp_path.resolve()
    assert settings.maps_dir == tmp_path.resolve() / "data" / "maps"
    monkeypatch.setenv("IMOB_MAPS_DIR", "/var/lib/imob/maps")
    assert AppSettings().maps_dir == Path("/var/lib/imob/maps")


def test_migrations_must_exist_under_the_app_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A wrong `IMOB_APP_ROOT` fails with a clear message before any database work."""
    monkeypatch.setenv("IMOB_APP_ROOT", str(tmp_path))
    with pytest.raises(ConfigurationError, match="IMOB_APP_ROOT"):
        run_migrations(create_engine("mysql+pymysql://u:p@127.0.0.1:1/x"))


_SECRET = "p@ss:w/rd%#?&=;' \\x"


def test_database_url_escapes_and_never_reveals_the_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """Special characters round-trip through the URL; repr, str and the label never show them."""
    monkeypatch.setenv("IMOB_MYSQL_USER", "imob")
    monkeypatch.setenv("IMOB_MYSQL_PASSWORD", _SECRET)
    monkeypatch.setenv("IMOB_MYSQL_HOST", "mysql")
    monkeypatch.setenv("IMOB_MYSQL_DATABASE", "imob")
    settings = AppSettings()
    url = settings.database_url

    assert make_url(url.render_as_string(hide_password=False)).password == _SECRET
    assert (url.drivername, url.host, url.port, url.database) == ("mysql+pymysql", "mysql", 3306, "imob")
    assert url.query == {"charset": "utf8mb4"}
    for shown in (repr(settings), str(settings), str(url), repr(url), settings.database_label):
        assert _SECRET not in shown
        assert "p@ss" not in shown
    assert settings.database_label == "mysql:3306/imob"


@pytest.mark.parametrize(
    ("user", "password", "missing"),
    [
        ("", "secret-value", "IMOB_MYSQL_USER"),
        ("imob", "", "IMOB_MYSQL_PASSWORD"),
        ("  ", "", "IMOB_MYSQL_USER, IMOB_MYSQL_PASSWORD"),
    ],
)
def test_missing_credentials_fail_closed_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch, user: str, password: str, missing: str
) -> None:
    """Startup refuses to run without credentials; the message names variables, never values."""
    monkeypatch.setenv("IMOB_MYSQL_USER", user)
    monkeypatch.setenv("IMOB_MYSQL_PASSWORD", password)
    settings = AppSettings()
    with pytest.raises(ConfigurationError) as caught:
        _ = settings.database_url
    assert caught.value.message == f"Missing required database setting(s): {missing}"
    assert "secret-value" not in str(caught.value)
    with pytest.raises(ConfigurationError):
        create_mysql_engine(settings)
    with pytest.raises(ConfigurationError):
        _run_serve(settings, host_arg=None, port_arg=None, no_timer=True, open_browser=False)


def test_cli_parser_supports_serve_browser_flag_and_port_fallback() -> None:
    """Verify `serve --open-browser` parses cleanly and `_find_available_port` returns a bindable port."""
    args = _build_parser().parse_args(["serve", "--open-browser", "--no-timer"])
    port = _find_available_port("127.0.0.1", 18950)

    assert (args.command, args.open_browser, args.no_timer) == ("serve", True, True)
    assert 18950 <= port < 18970


def test_sync_schedule_and_timezone_defaults_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default schedule is 06:00,18:00 America/Sao_Paulo; invalid HH:MM CSV or timezone is rejected."""
    monkeypatch.delenv("IMOB_SYNC_TIMES", raising=False)
    monkeypatch.delenv("IMOB_SYNC_TIMEZONE", raising=False)

    defaults = AppSettings()
    assert defaults.sync_schedule == (time(6, 0), time(18, 0))
    assert defaults.sync_timezone == "America/Sao_Paulo"
    assert defaults.auto_sync_enabled is True
    assert defaults.sync_interval_hours == 6.0
    assert not hasattr(defaults, "timer_check_minutes")

    monkeypatch.setenv("IMOB_SYNC_TIMES", " 18:00 , 06:00 , 06:00 ")
    monkeypatch.setenv("IMOB_SYNC_TIMEZONE", "America/Sao_Paulo")
    custom = AppSettings()
    assert custom.sync_schedule == (time(6, 0), time(18, 0))

    for bad_times in ("", "   ", "6:00", "06:00:00", "24:00", "06:60", "06:00,", "06:00,abc"):
        monkeypatch.setenv("IMOB_SYNC_TIMES", bad_times)
        with pytest.raises(ValidationError):
            AppSettings()

    monkeypatch.setenv("IMOB_SYNC_TIMES", "06:00,18:00")
    for bad_tz in ("", "   ", "Mars/Phobos", "Not/A_Zone"):
        monkeypatch.setenv("IMOB_SYNC_TIMEZONE", bad_tz)
        with pytest.raises(ValidationError):
            AppSettings()


def test_next_run_at_computes_sao_paulo_slots_and_handles_dst_transitions() -> None:
    """Verify 05:59 -> 06:00, 06:00:01 -> 18:00, 18:30 -> next day 06:00, plus DST gap/fold handling."""
    sp = ZoneInfo("America/Sao_Paulo")
    slots = (time(6, 0), time(18, 0))

    # 05:59 BRT (08:59 UTC) -> same day 06:00 BRT (09:00 UTC)
    at_0559 = datetime(2026, 11, 25, 5, 59, 0, tzinfo=sp).astimezone(UTC)
    assert next_run_at(at_0559, slots, sp) == datetime(2026, 11, 25, 6, 0, 0, tzinfo=sp).astimezone(UTC)

    # Exact 06:00:00 BRT and 06:00:01 BRT -> same day 18:00 BRT (21:00 UTC)
    at_0600 = datetime(2026, 11, 25, 6, 0, 0, tzinfo=sp).astimezone(UTC)
    at_060001 = datetime(2026, 11, 25, 6, 0, 1, tzinfo=sp).astimezone(UTC)
    expected_1800 = datetime(2026, 11, 25, 18, 0, 0, tzinfo=sp).astimezone(UTC)
    assert next_run_at(at_0600, slots, sp) == expected_1800
    assert next_run_at(at_060001, slots, sp) == expected_1800

    # 18:30 BRT -> next day 06:00 BRT
    at_1830 = datetime(2026, 11, 25, 18, 30, 0, tzinfo=sp).astimezone(UTC)
    assert next_run_at(at_1830, slots, sp) == datetime(2026, 11, 26, 6, 0, 0, tzinfo=sp).astimezone(UTC)

    # DST spring-forward gap in America/New_York (2026-03-08 02:00 -> 03:00):
    # A 02:30 slot resolves to the 03:00 EDT (07:00 UTC) transition before the gap, and is already
    # past once the clock is at 03:05 EDT (07:05 UTC).
    ny = ZoneInfo("America/New_York")
    before_gap = datetime(2026, 3, 8, 6, 50, 0, tzinfo=UTC)  # 01:50 EST
    after_gap = datetime(2026, 3, 8, 7, 5, 0, tzinfo=UTC)  # 03:05 EDT
    assert next_run_at(before_gap, (time(2, 30), time(18, 0)), ny) == datetime(
        2026, 3, 8, 7, 0, 0, tzinfo=UTC
    )
    assert next_run_at(after_gap, (time(2, 30), time(18, 0)), ny) == datetime(
        2026, 3, 8, 22, 0, 0, tzinfo=UTC
    )

    # DST fall-back fold in America/New_York (2026-11-01 01:30 at 05:30 UTC fold=0 and 06:30 UTC fold=1):
    # Fires once at fold=0 (05:30 UTC); after 05:30 UTC it advances to 18:00 EST (23:00 UTC).
    before_fold = datetime(2026, 11, 1, 5, 0, 0, tzinfo=UTC)
    after_first_fold = datetime(2026, 11, 1, 5, 30, 1, tzinfo=UTC)
    assert next_run_at(before_fold, (time(1, 30), time(18, 0)), ny) == datetime(
        2026, 11, 1, 5, 30, 0, tzinfo=UTC
    )
    assert next_run_at(after_first_fold, (time(1, 30), time(18, 0)), ny) == datetime(
        2026, 11, 1, 23, 0, 0, tzinfo=UTC
    )


class _DummyLease:
    def release(self) -> None:
        return None


class _DummyRepo:
    def try_acquire_sync_lock(self) -> _DummyLease:
        return _DummyLease()

    def seed_agencies(self, agencies: object, *, sync_lock_held: bool = False) -> None:
        _ = (agencies, sync_lock_held)

    def list_agencies(self) -> tuple[()]:
        return ()


class _DummyScrapers:
    async def fetch_catalog(self, agency: AgencyConfig, *, max_pages: int) -> ScrapeBatchResult:
        _ = (agency, max_pages)
        return ScrapeBatchResult(items=(), requests_made=1)

    async def fetch_property_detail(self, agency: AgencyConfig, source_url: str) -> PropertyDetailPatch:
        raise AssertionError("not used")

    def get_scraper(self, platform: PlatformKind) -> _DummyScrapers:
        _ = platform
        return self


@pytest.mark.asyncio
async def test_scheduler_bounded_rechecks_clock_jumps_and_no_boot_catchup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify no run on boot, bounded sleeps, no duplicate on backward jump, and skip on forward jump."""
    sp = ZoneInfo("America/Sao_Paulo")
    # Boot at 06:01 BRT (1 minute after 06:00): no catch-up on boot, next run is 18:00 BRT (21:00 UTC).
    current_utc = datetime(2026, 11, 25, 6, 1, 0, tzinfo=sp).astimezone(UTC)
    sleep_calls: list[float] = []
    fired_at: list[datetime] = []
    step = 0

    async def fake_sleep(seconds: float) -> None:
        nonlocal current_utc, step
        sleep_calls.append(seconds)
        step += 1
        if step == 1:
            # Bounded recheck chunk (60s), then jump closer to 18:00 BRT (17:59:30 BRT = 20:59:30 UTC).
            assert seconds == 60.0
            current_utc = datetime(2026, 11, 25, 17, 59, 30, tzinfo=sp).astimezone(UTC)
        elif step == 2:
            # Remaining is 30s; simulate backward clock jump of 2 minutes during sleep to 17:57:30 BRT.
            assert seconds == 30.0
            current_utc = datetime(2026, 11, 25, 17, 57, 30, tzinfo=sp).astimezone(UTC)
        elif step == 3:
            # Still before 18:00 BRT after backward jump (did not fire early!); now reach 18:00:00 BRT.
            assert seconds == 60.0
            current_utc = datetime(2026, 11, 25, 18, 0, 0, tzinfo=sp).astimezone(UTC)
        elif step == 4:
            # 18:00 BRT just fired! Simulate backward clock jump to 17:55 BRT: must NOT re-fire 18:00 BRT.
            current_utc = datetime(2026, 11, 25, 17, 55, 0, tzinfo=sp).astimezone(UTC)
        elif step == 5:
            # Cross 18:00 again after the backward jump: no duplicate run.
            current_utc = datetime(2026, 11, 25, 18, 0, 5, tzinfo=sp).astimezone(UTC)
        elif step == 6:
            # Simulate a forward jump past the next morning's slot.
            # The missed 06:00 BRT slot (>300s late) must be skipped without firing!
            current_utc = datetime(2026, 11, 26, 8, 0, 0, tzinfo=sp).astimezone(UTC)
        else:
            raise asyncio.CancelledError

    svc = SyncService(
        _DummyRepo(),  # type: ignore[arg-type]
        _DummyScrapers(),
        AppSettings(),
        now_fn=lambda: current_utc,
        sleep_fn=fake_sleep,
        max_sleep_seconds=60.0,
        max_slot_lateness_seconds=300.0,
    )

    async def record_sync(*, agency_slugs: tuple[str, ...] = (), force: bool = False) -> object:
        assert agency_slugs == ()
        assert force is True
        fired_at.append(current_utc)
        if len(fired_at) == 1:
            raise DomainValidationError("Simulated error swallowed by scheduler")
        return object()

    monkeypatch.setattr(svc, "sync_agencies", record_sync)
    svc.start_periodic_timer()
    assert svc.get_runtime_status().next_scheduled_run_at == datetime(
        2026, 11, 25, 18, 0, 0, tzinfo=sp
    ).astimezone(UTC)

    assert svc._timer_task is not None
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(svc._timer_task, timeout=5)

    # Fired only once (at 2026-11-25 18:00 BRT), never on boot, never duplicated on backward jump,
    # and skipped the missed 2026-11-26 06:00 BRT slot after the forward jump to 08:00 BRT.
    assert fired_at == [datetime(2026, 11, 25, 18, 0, 0, tzinfo=sp).astimezone(UTC)]
    assert svc.get_runtime_status().next_scheduled_run_at == datetime(
        2026, 11, 26, 18, 0, 0, tzinfo=sp
    ).astimezone(UTC)
    await svc.stop_periodic_timer()
    assert svc.get_runtime_status().next_scheduled_run_at is None

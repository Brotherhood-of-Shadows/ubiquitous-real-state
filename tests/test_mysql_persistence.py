"""MySQL-specific persistence behavior: schema health, FULLTEXT, precision, UTC, identity, and locks."""

from __future__ import annotations

import asyncio
import contextlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from threading import Barrier, Event

import httpx
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, pool, select, text
from sqlalchemy.exc import OperationalError

from imob.api.app import create_app
from imob.config import AppSettings
from imob.db.engine import create_mysql_engine, create_session_factory, run_migrations
from imob.db.locks import MySqlNamedLock
from imob.db.orm import Base, PriceHistoryRow, PropertyRow
from imob.db.repository import (
    SqlAlchemyPropertyRepository,
    _build_fts_match_expression,
    _ordered_pair,
)
from imob.domain.enums import SyncStatus
from imob.domain.models import (
    AgencyConfig,
    ListingKey,
    NormalizedListing,
    PropertyDetailPatch,
    PropertyFilterParams,
    RawScrapedItem,
)
from imob.domain.normalizer import normalize_scraped_item
from imob.domain.registry import AGENCIES_BY_SLUG
from imob.errors import DomainValidationError, PersistenceError
from imob.ports import ScrapeBatchResult
from imob.services.sync_service import SyncService
from tests.conftest import MySqlTestDatabase

_AGENCY = AGENCIES_BY_SLUG["imobilli"]
_OTHER_AGENCY = AGENCIES_BY_SLUG["famex"]


def _listing(agency: AgencyConfig = _AGENCY, /, **raw: object) -> NormalizedListing:
    fields: dict[str, object] = {
        "agency_slug": agency.slug,
        "external_id": "1",
        "source_url": f"https://{agency.slug}.example/1",
        "raw_purpose": "venda",
        "raw_type": "Casa",
        "raw_city": "Pato Branco",
        "raw_neighborhood": "Centro",
        "title": "Casa ampla",
        "sale_price": 500000.0,
    }
    fields.update(raw)
    listing = normalize_scraped_item(RawScrapedItem.model_validate(fields), agency)
    assert listing is not None
    return listing


def _store(repo: SqlAlchemyPropertyRepository, agency: AgencyConfig, *items: NormalizedListing) -> None:
    repo.apply_agency_catalog(
        agency,
        items,
        allow_deactivation=False,
        sync_status=SyncStatus.SUCCESS,
        requests_made=1,
        duration_seconds=0,
    )


def _repo(db: MySqlTestDatabase) -> SqlAlchemyPropertyRepository:
    repo = SqlAlchemyPropertyRepository(create_session_factory(db.engine))
    repo.seed_agencies((_AGENCY, _OTHER_AGENCY))
    return repo


def _search(repo: SqlAlchemyPropertyRepository, **filters: object) -> list[str]:
    page = repo.search_properties(PropertyFilterParams.model_validate(filters))
    return sorted(item.external_id for item in page.items)


# --- schema / migration health -------------------------------------------------------------------


def test_baseline_matches_orm_metadata_and_mysql_types(mysql_db: MySqlTestDatabase) -> None:
    """The migrated schema has no drift from the ORM and uses lossless MySQL types."""
    with mysql_db.engine.connect() as conn:
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "0001_mysql_baseline"
        diffs = compare_metadata(MigrationContext.configure(conn), Base.metadata)
        columns = {
            (table, column): (column_type, collation)
            for table, column, column_type, collation in conn.execute(
                text(
                    "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, COLLATION_NAME "
                    "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()"
                )
            )
        }
        tables: dict[str, str] = dict(
            conn.execute(
                text(
                    "SELECT TABLE_NAME, CONCAT(ENGINE, '/', TABLE_COLLATION) "
                    "FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME <> 'alembic_version'"
                )
            ).all()
        )
        fulltext = conn.execute(
            text(
                "SELECT COLUMN_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() "
                "AND INDEX_NAME = 'ft_properties_search' ORDER BY SEQ_IN_INDEX"
            )
        ).scalars()
        checks = set(
            conn.execute(
                text(
                    "SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND CONSTRAINT_TYPE = 'CHECK'"
                )
            ).scalars()
        )

        assert diffs == []
        assert set(tables.values()) == {"InnoDB/utf8mb4_0900_ai_ci"}
        assert len(tables) == 5
        assert list(fulltext) == [
            "title",
            "neighborhood",
            "street",
            "building_name",
            "ref_code",
            "description",
        ]
    assert columns["properties", "sale_price"][0] == "double"
    assert columns["properties", "latitude"][0] == "double"
    assert columns["price_history", "rent_price"][0] == "double"
    assert columns["properties", "first_seen_at"][0] == "datetime(6)"
    assert columns["sync_runs", "finished_at"][0] == "datetime(6)"
    assert columns["properties", "description"][0] == "mediumtext"
    assert columns["properties", "image_urls_json"][0] == "mediumtext"
    for key in (
        ("agencies", "slug"),
        ("properties", "agency_slug"),
        ("properties", "external_id"),
        ("properties", "dedup_group_key"),
        ("sync_runs", "agency_slug"),
    ):
        assert columns[key][1] == "utf8mb4_0900_bin", key
    assert columns["properties", "title"][1] == "utf8mb4_0900_ai_ci"
    assert not {"is_favorite", "is_hidden", "user_notes"} & {c for t, c in columns if t == "properties"}
    assert "ck_match_order" not in checks
    assert {"ck_match_level", "ck_match_score", "ck_properties_sale_pos"} <= checks


def test_migrations_are_idempotent_and_downgrade_cleanly(mysql_db: MySqlTestDatabase) -> None:
    """Re-running head is a no-op; base drops every table; upgrading again restores the schema."""
    from alembic import command
    from alembic.config import Config

    from imob.db.engine import _migrations_dir

    run_migrations(mysql_db.engine)
    migrations = _migrations_dir()
    cfg = Config()
    cfg.set_main_option("script_location", str(migrations))
    cfg.set_main_option("path_separator", "os")
    cfg.set_main_option("version_locations", str(migrations / "mysql_versions"))
    with mysql_db.engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "base")
    with mysql_db.engine.connect() as conn:
        remaining = set(
            conn.execute(
                text("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()")
            ).scalars()
        )
    assert remaining == {"alembic_version"}
    # Both connections start against the downgraded schema. Neither should race the other's DDL.
    barrier = Barrier(2)

    def upgrade() -> None:
        barrier.wait(timeout=5)
        run_migrations(mysql_db.engine)

    with ThreadPoolExecutor(max_workers=2) as executor:
        upgrades = [executor.submit(upgrade) for _ in range(2)]
        for result in upgrades:
            result.result(timeout=15)
    repo = _repo(mysql_db)
    _store(repo, _AGENCY, _listing())
    assert _search(repo) == ["1"]


def test_similarity_pairs_are_stored_in_ascending_order() -> None:
    """The order the dropped CHECK used to enforce is normalized in code."""
    assert _ordered_pair(9, 3) == (3, 9)
    assert _ordered_pair(3, 9) == (3, 9)
    with pytest.raises(PersistenceError):
        _ordered_pair(4, 4)


def test_seeding_agencies_rebuilds_matches_under_sync_lock(
    mysql_db: MySqlTestDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Startup seeding rebuilds similarity groups while holding the database-scoped `imob_sync` lock."""
    import imob.db.repository as repo_mod

    repo = _repo(mysql_db)
    other_engine = create_mysql_engine(mysql_db.settings)
    probe_lock = MySqlNamedLock(other_engine, "imob_sync")
    holder_lock = MySqlNamedLock(other_engine, "imob_sync")
    real_rebuild = repo_mod._rebuild_similarity_groups
    lock_busy_during_rebuild: list[bool] = []

    def checked_rebuild(session: object) -> None:
        acquired = probe_lock.try_acquire()
        if acquired:
            probe_lock.release()
        lock_busy_during_rebuild.append(not acquired)
        real_rebuild(session)  # type: ignore[arg-type]

    try:
        monkeypatch.setattr(repo_mod, "_rebuild_similarity_groups", checked_rebuild)
        repo.seed_agencies((_AGENCY.model_copy(update={"name": "Renamed"}),))
        assert lock_busy_during_rebuild == [True]
        assert any(agency.name == "Renamed" for agency in repo.list_agencies())
        # The sync lock is released on return, and sync_lock_held=True skips re-acquiring it.
        assert holder_lock.try_acquire()
        repo.seed_agencies((_AGENCY.model_copy(update={"name": "Renamed Twice"}),), sync_lock_held=True)
        assert lock_busy_during_rebuild == [True, True]
        assert any(agency.name == "Renamed Twice" for agency in repo.list_agencies())
    finally:
        probe_lock.release()
        holder_lock.release()
        other_engine.dispose()


# --- FULLTEXT search ---------------------------------------------------------------------------------


def test_fulltext_expression_is_boolean_prefix_and_operator_safe() -> None:
    """Every token is required as a prefix; operators cannot change the query structure."""
    assert _build_fts_match_expression("Água  la") == "+Água* +la*"
    assert _build_fts_match_expression('+casa -"centro" (piscina)~ @2 x*') == "+casa* +centro* +piscina*"
    assert _build_fts_match_expression("a b c") is None
    assert _build_fts_match_expression('"+-<>()~*@') is None
    assert _build_fts_match_expression(" ".join(f"t{i}" for i in range(12))) == " ".join(
        f"+t{i}*" for i in range(8)
    )


def test_fulltext_is_accent_case_insensitive_prefix_and_indexes_short_tokens(
    mysql_db: MySqlTestDatabase,
) -> None:
    """Mirror the old full-text behavior: diacritics folded, prefixes, AND semantics, 2-letter words."""
    repo = _repo(mysql_db)
    _store(
        repo,
        _AGENCY,
        _listing(external_id="agua", title="Casa no bairro Água Verde"),
        _listing(external_id="salle", title="Apartamento no Edifício La Salle", raw_neighborhood="Centro"),
        _listing(
            external_id="desc",
            title="Sobrado",
            description="Próximo à praça São João, com churrasqueira.",
        ),
        _listing(external_id="Ref-X", ref_code="PB-778", title="Terreno plano"),
        _listing(external_id="ref-x", title="Barracão"),
    )

    assert _search(repo, q="agua") == ["agua"]
    assert _search(repo, q="ÁGUA verde") == ["agua"]
    assert _search(repo, q="Ag") == ["agua"]
    assert _search(repo, q="la salle") == ["salle"]
    assert _search(repo, q="sal") == ["salle"]
    assert _search(repo, q="Sao Joao churrasq") == ["desc"]
    assert _search(repo, q="agua salle") == []
    assert _search(repo, q="edificio") == ["salle"]
    # Neighborhood and ref_code are part of the index; the ref_code also matches exactly.
    assert _search(repo, q="pb-778") == ["Ref-X"]
    assert "salle" in _search(repo, q="centro")
    # Operators and one-letter input never raise; the exact external-ID fallback is case-sensitive.
    assert _search(repo, q='"+-<>()~*@') == []
    assert _search(repo, q="Ref-X") == ["Ref-X"]
    assert _search(repo, q="x") == []


# --- precision, timestamps, identity -------------------------------------------------------------------


def test_prices_areas_and_coordinates_round_trip_exactly(mysql_db: MySqlTestDatabase) -> None:
    """DOUBLE keeps cents on large prices (4-byte FLOAT would round 1 250 000.55)."""
    repo = _repo(mysql_db)
    _store(
        repo,
        _AGENCY,
        _listing(
            sale_price=1250000.55, area_private_m2=123.45, latitude=-26.2287654, longitude=-52.6712345
        ),
    )
    page = repo.search_properties(PropertyFilterParams())
    detail = repo.get_property_detail(page.items[0].id)
    assert detail.sale_price == 1250000.55
    assert detail.area_private_m2 == 123.45
    assert (detail.latitude, detail.longitude) == (-26.2287654, -52.6712345)
    assert detail.price_history[0].sale_price == 1250000.55
    assert _search(repo, min_price=1250000.55, max_price=1250000.55) == ["1"]
    with mysql_db.engine.connect() as conn:
        assert conn.scalar(select(PriceHistoryRow.sale_price)) == 1250000.55


def test_timestamps_round_trip_in_utc_with_microseconds(mysql_db: MySqlTestDatabase) -> None:
    """Aware values are stored as UTC regardless of their offset; reads are UTC-aware."""
    repo = _repo(mysql_db)
    _store(repo, _AGENCY, _listing())
    brt = timezone(timedelta(hours=-3))
    local = datetime(2026, 11, 25, 6, 0, 0, 123456, tzinfo=brt)
    factory = create_session_factory(mysql_db.engine)
    with factory() as session, session.begin():
        row = session.scalars(select(PropertyRow)).one()
        row.first_seen_at = local
    with factory() as session:
        stored = session.scalars(select(PropertyRow)).one().first_seen_at
        raw = session.scalar(text("SELECT CAST(first_seen_at AS CHAR) FROM properties"))
        session_tz = session.scalar(text("SELECT @@session.time_zone"))
    assert stored == local
    assert stored.tzinfo is UTC
    assert raw == "2026-11-25 09:00:00.123456"
    assert session_tz == "+00:00"
    with factory() as session, pytest.raises(Exception, match="Naive datetime"), session.begin():
        session.scalars(select(PropertyRow)).one().last_seen_at = datetime(2026, 1, 1)  # noqa: DTZ001
    summary = repo.search_properties(PropertyFilterParams()).items[0]
    assert summary.first_seen_at == local and summary.first_seen_at.utcoffset() == timedelta(0)


def test_listing_identity_is_case_and_trailing_space_sensitive(mysql_db: MySqlTestDatabase) -> None:
    """IDs differing only by case or a trailing space are distinct listings, like in the source sites."""
    repo = _repo(mysql_db)
    ids = ("ab12", "AB12", "ab12 ", "ábc")
    # Exercise storage identity directly: the normalizer strips surrounding whitespace from raw IDs.
    items = tuple(
        _listing(external_id=ext, title=f"Casa {i}").model_copy(update={"external_id": ext})
        for i, ext in enumerate(ids)
    )
    _store(repo, _AGENCY, *items)
    # Re-applying the same catalog updates in place: no duplicate-key error and no new rows.
    _store(repo, _AGENCY, *items)
    assert _search(repo) == sorted(ids)
    assert repo.get_stats().active_properties == 4

    key = ListingKey(agency_slug=_AGENCY.slug, external_id="ab12")
    assert _search(repo, include_keys=(key,)) == ["ab12"]
    assert _search(repo, exclude_keys=(key,)) == sorted(set(ids) - {"ab12"})
    assert _search(repo, include_keys=(ListingKey(agency_slug=_AGENCY.slug, external_id="abc"),)) == []
    assert _search(repo, q="AB12") == ["AB12"]


# --- dedup ranking and facets --------------------------------------------------------------------------


def test_dedup_keeps_ungrouped_listings_and_picks_cheapest_in_group(mysql_db: MySqlTestDatabase) -> None:
    """Ungrouped rows each get their own `unique-<id>` partition; a group collapses to its cheapest row."""
    repo = _repo(mysql_db)
    same_flat = {
        "raw_type": "Apartamento",
        "title": "Apartamento Edifício Andressa",
        "area_private_m2": 119,
        "bedrooms": 3,
        "suites": 2,
        "bathrooms": 3,
        "parking_spots": 1,
    }
    _store(
        repo,
        _AGENCY,
        _listing(external_id="g1", sale_price=720000.0, **same_flat),
        _listing(external_id="u1", title="Casa térrea", raw_neighborhood="Fraron", sale_price=300000.0),
        _listing(external_id="u2", title="Terreno", raw_type="Terreno", sale_price=150000.0),
    )
    _store(repo, _OTHER_AGENCY, _listing(_OTHER_AGENCY, external_id="g1", sale_price=700000.0, **same_flat))

    assert repo.get_stats().duplicate_groups_count == 1
    page = repo.search_properties(PropertyFilterParams(deduplicate=True))
    picked = sorted((item.agency_slug, item.external_id, item.duplicate_count) for item in page.items)
    assert page.total == 3
    assert picked == [
        (_OTHER_AGENCY.slug, "g1", 2),
        (_AGENCY.slug, "u1", 1),
        (_AGENCY.slug, "u2", 1),
    ]
    excluded = ListingKey(agency_slug=_OTHER_AGENCY.slug, external_id="g1")
    again = repo.search_properties(PropertyFilterParams(deduplicate=True, exclude_keys=(excluded,)))
    assert sorted((i.agency_slug, i.external_id) for i in again.items) == [
        (_AGENCY.slug, "g1"),
        (_AGENCY.slug, "u1"),
        (_AGENCY.slug, "u2"),
    ]


def test_facets_group_by_slug_and_count_active_rows(mysql_db: MySqlTestDatabase) -> None:
    """Facets count active listings per type, neighborhood slug and agency."""
    repo = _repo(mysql_db)
    _store(
        repo,
        _AGENCY,
        _listing(external_id="1", raw_neighborhood="Centro"),
        _listing(external_id="2", raw_neighborhood="CENTRO", raw_purpose="aluguel", rent_price=1800.0),
        _listing(external_id="3", raw_neighborhood="Brasília", raw_type="Terreno"),
    )
    _store(repo, _OTHER_AGENCY, _listing(_OTHER_AGENCY, external_id="9", raw_neighborhood="Brasilia"))
    facets = repo.get_facets()
    neighborhoods = {bucket.value: bucket.count for bucket in facets.neighborhoods}
    assert neighborhoods == {"centro": 2, "brasilia": 2}
    assert {b.value: b.count for b in facets.agencies} == {_AGENCY.slug: 3, _OTHER_AGENCY.slug: 1}
    assert sum(b.count for b in facets.property_types) == 4
    rent = repo.get_facets(purpose="aluguel")
    assert {b.value: b.count for b in rent.neighborhoods} == {"centro": 1}


def test_detail_patch_and_similarity_rebuild_work_on_mysql(mysql_db: MySqlTestDatabase) -> None:
    """The operator-only detail patch path still commits and rebuilds groups."""
    repo = _repo(mysql_db)
    _store(repo, _AGENCY, _listing())
    prop_id = repo.search_properties(PropertyFilterParams()).items[0].id
    patched = repo.apply_detail_patch(prop_id, PropertyDetailPatch(condo_fee=350.5, description="Nova"))
    assert (patched.condo_fee, patched.description) == (350.5, "Nova")
    assert patched.detail_enriched_at is not None and patched.detail_enriched_at.tzinfo is UTC


# --- cross-process sync lock -----------------------------------------------------------------------------


def test_named_lock_is_exclusive_per_database_and_reusable(
    mysql_db: MySqlTestDatabase, mysql_admin_engine: Engine
) -> None:
    """A second session cannot take the lock until it is released; other databases are unaffected."""
    first = MySqlNamedLock(mysql_db.engine, "imob_sync")
    second_engine = create_mysql_engine(mysql_db.settings)
    other_db = create_engine(mysql_admin_engine.url.set(database="mysql"), poolclass=pool.NullPool)
    try:
        second = MySqlNamedLock(second_engine, "imob_sync")
        elsewhere = MySqlNamedLock(other_db, "imob_sync")
        assert first.name == second.name
        assert len(first.name) <= 64
        assert first.try_acquire()
        assert not second.try_acquire()
        assert elsewhere.try_acquire()
        elsewhere.release()
        first.release()
        first.release()  # idempotent
        assert second.try_acquire()
        assert not first.try_acquire()
        second.release()
    finally:
        second_engine.dispose()
        other_db.dispose()


def test_named_lock_uses_the_entire_database_name() -> None:
    prefix = "a" * 63
    first = create_engine(f"mysql+pymysql://localhost/{prefix}1")
    second = create_engine(f"mysql+pymysql://localhost/{prefix}2")
    assert MySqlNamedLock(first, "imob_sync").name != MySqlNamedLock(second, "imob_sync").name


class _BlockingScraper:
    """Scraper double that waits until released, keeping the first sync inside its lock."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def fetch_catalog(self, agency: AgencyConfig, *, max_pages: int) -> ScrapeBatchResult:
        _ = (agency, max_pages)
        self.started.set()
        await self.release.wait()
        return ScrapeBatchResult(items=(), requests_made=1)

    async def fetch_property_detail(self, agency: AgencyConfig, source_url: str) -> PropertyDetailPatch:
        raise AssertionError("detail pages are never fetched")

    def get_scraper(self, platform: object) -> _BlockingScraper:
        _ = platform
        return self


@pytest.mark.asyncio
async def test_second_process_sync_is_rejected_while_one_runs(mysql_db: MySqlTestDatabase) -> None:
    """Two services on separate engines (as CLI and server would be) never sync at the same time."""
    settings = mysql_db.settings
    scraper = _BlockingScraper()
    first = SyncService(_repo(mysql_db), scraper, settings)
    other_engine = create_mysql_engine(settings)
    try:
        second = SyncService(
            SqlAlchemyPropertyRepository(create_session_factory(other_engine)), scraper, settings
        )
        running = asyncio.create_task(first.sync_agencies(agency_slugs=(_AGENCY.slug,), force=True))
        await asyncio.wait_for(scraper.started.wait(), timeout=10)
        with pytest.raises(DomainValidationError, match="already in progress"):
            await second.sync_agencies(agency_slugs=(_AGENCY.slug,), force=True)
        scraper.release.set()
        report = await asyncio.wait_for(running, timeout=10)
        assert report.agencies[0].agency_slug == _AGENCY.slug
        # The lock is free again once the first run finishes.
        follow_up = await second.sync_agencies(agency_slugs=(_AGENCY.slug,), force=True)
        assert len(follow_up.agencies) == 1
    finally:
        scraper.release.set()
        other_engine.dispose()


class _SingleListingScraper:
    """Scraper double that returns one valid Pato Branco listing immediately."""

    async def fetch_catalog(self, agency: AgencyConfig, *, max_pages: int) -> ScrapeBatchResult:
        _ = max_pages
        item = RawScrapedItem(
            agency_slug=agency.slug,
            external_id="cancel-1",
            source_url=f"https://{agency.slug}.example/cancel-1",
            raw_purpose="venda",
            raw_type="Casa",
            raw_city="Pato Branco",
            raw_neighborhood="Centro",
            title="Casa durante cancelamento",
            sale_price=450000.0,
        )
        return ScrapeBatchResult(items=(item,), requests_made=1)

    async def fetch_property_detail(self, agency: AgencyConfig, source_url: str) -> PropertyDetailPatch:
        raise AssertionError("detail pages are never fetched")

    def get_scraper(self, platform: object) -> _SingleListingScraper:
        _ = platform
        return self


@pytest.mark.asyncio
async def test_cancelled_sync_drains_active_write_before_releasing_mysql_lock(
    mysql_db: MySqlTestDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancelling sync (even repeatedly) keeps the MySQL lease held until in-flight DB writes finish."""
    settings = mysql_db.settings
    repo = _repo(mysql_db)
    scraper = _SingleListingScraper()
    first = SyncService(repo, scraper, settings)
    other_engine = create_mysql_engine(settings)
    competing_lock = MySqlNamedLock(other_engine, "imob_sync")
    second = SyncService(
        SqlAlchemyPropertyRepository(create_session_factory(other_engine)), scraper, settings
    )
    real_apply = repo.apply_agency_catalog
    write_entered = Event()
    release_write = Event()
    write_finished = Event()
    running: asyncio.Task[object] | None = None

    def blocked_apply(*args: object, **kwargs: object) -> object:
        write_entered.set()
        if not release_write.wait(timeout=10):
            raise RuntimeError("Timed out waiting for test to release blocked catalog write")
        try:
            return real_apply(*args, **kwargs)  # type: ignore[arg-type]
        finally:
            write_finished.set()

    try:
        monkeypatch.setattr(repo, "apply_agency_catalog", blocked_apply)
        running = asyncio.create_task(first.sync_agencies(agency_slugs=(_AGENCY.slug,), force=True))
        assert await asyncio.to_thread(write_entered.wait, 10)

        # Repeated cancellation while the catalog write thread is still blocked must not release the lease.
        running.cancel()
        await asyncio.sleep(0)
        running.cancel()
        await asyncio.sleep(0)

        assert not running.done()
        assert not write_finished.is_set()
        assert not competing_lock.try_acquire()
        with pytest.raises(DomainValidationError, match="already in progress"):
            await second.sync_agencies(agency_slugs=(_AGENCY.slug,), force=True)

        release_write.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(running, timeout=10)

        assert write_finished.is_set()
        assert _search(repo) == ["cancel-1"]
        assert competing_lock.try_acquire()
    finally:
        release_write.set()
        if running is not None and not running.done():
            running.cancel()
        if running is not None:
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(running, timeout=10)
        competing_lock.release()
        other_engine.dispose()


# --- health and secret handling --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_reports_503_when_mysql_is_unreachable(mysql_db: MySqlTestDatabase) -> None:
    """The compose healthcheck sees the database outage instead of a false 200."""
    app = create_app(mysql_db.settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client,
    ):
        assert (await client.get("/api/health")).status_code == 200
        healthy_engine = app.state.engine
        dead = create_mysql_engine(mysql_db.settings.model_copy(update={"mysql_port": 1}))
        app.state.engine = dead
        try:
            res = await client.get("/api/health")
        finally:
            app.state.engine = healthy_engine
            dead.dispose()
    assert res.status_code == 503
    assert res.json()["status"] == "unavailable"


def test_wrong_password_error_never_contains_the_password(mysql_db: MySqlTestDatabase) -> None:
    """Driver errors name the user but never echo the attempted secret."""
    secret = "wr0ng-S3cret:@/%?"
    settings: AppSettings = mysql_db.settings.model_copy(update={"mysql_password": SecretStr(secret)})
    engine = create_mysql_engine(settings)
    try:
        with pytest.raises(OperationalError) as caught, engine.connect():
            pass
    finally:
        engine.dispose()
    text_seen = f"{caught.value}\n{caught.value!r}\n{caught.value.orig}"
    assert "Access denied" in text_seen
    assert secret not in text_seen
    assert "S3cret" not in text_seen

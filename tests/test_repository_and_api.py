"""Integration tests for MySQL persistence, Alembic + FULLTEXT, SyncService, and FastAPI routes."""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import httpx
import pytest

from imob.api.app import create_app
from imob.db.engine import create_session_factory
from imob.db.repository import SqlAlchemyPropertyRepository
from imob.domain.enums import FurnishedStatus, PlatformKind, SyncStatus
from imob.domain.models import (
    AgencyConfig,
    NormalizedListing,
    PropertyDetailPatch,
    PropertyFilterParams,
    RawScrapedItem,
)
from imob.ports import ScrapeBatchResult, UpsertBatchOutcome
from imob.services.sync_service import SyncService
from tests.conftest import MySqlTestDatabase


class FakePlatformScraper:
    """In-memory deterministic scraper double keyed by agency slug."""

    def __init__(self, batches: dict[str, list[RawScrapedItem]]) -> None:
        """Initialize with mutable per-agency raw listing snapshots."""
        self.batches = batches
        self.detail_calls = 0

    async def fetch_catalog(self, agency: AgencyConfig, *, max_pages: int) -> ScrapeBatchResult:
        """Return configured fake batch for `agency.slug`."""
        _ = max_pages
        items = tuple(self.batches.get(agency.slug, []))
        return ScrapeBatchResult(items=items, requests_made=1)

    async def fetch_property_detail(self, agency: AgencyConfig, source_url: str) -> PropertyDetailPatch:
        """Return deterministic detail enrichment payload."""
        _ = (agency, source_url)
        self.detail_calls += 1
        return PropertyDetailPatch(
            description="Descrição completa enriquecida sob demanda com churrasqueira e piscina.",
            condo_fee=380.0,
            iptu_fee=110.0,
            furnished_status=FurnishedStatus.FURNISHED,
            has_pool=True,
            image_urls=(
                "https://arcariimoveis.imb.br/f1.jpg",
                "https://arcariimoveis.imb.br/f2.jpg",
            ),
        )


class FakeScraperRegistry:
    """Scraper registry returning `FakePlatformScraper` for every platform."""

    def __init__(self, scraper: FakePlatformScraper) -> None:
        """Initialize with single fake scraper."""
        self._scraper = scraper

    def get_scraper(self, platform: PlatformKind) -> FakePlatformScraper:
        """Return fake scraper regardless of platform."""
        _ = platform
        return self._scraper


@pytest.mark.asyncio
async def test_sync_lifecycle_price_history_circuit_breaker_dedup_fts_and_api(
    mysql_db: MySqlTestDatabase,
) -> None:
    """Verify full lifecycle across migrations, sync updates, price drops, dedup, FULLTEXT, and HTTP API."""
    settings = mysql_db.settings.model_copy(update={"sync_interval_hours": 6.0})
    engine = mysql_db.engine
    session_factory = create_session_factory(engine)
    repo = SqlAlchemyPropertyRepository(session_factory)

    initial_batches: dict[str, list[RawScrapedItem]] = {
        "arcari": [
            RawScrapedItem(
                agency_slug="arcari",
                external_id="101",
                ref_code="AR101",
                source_url="https://arcariimoveis.imb.br/101-apto-centro",
                raw_purpose="aluguel",
                raw_type="Apartamento",
                title="Apartamento 2 quartos no Edifício Solar Iguaçu - Centro apto 101",
                street="Rua Iguaçu",
                street_number="100",
                raw_city="Pato Branco",
                raw_neighborhood="Centro",
                rent_price=2500.0,
                area_private_m2=80.0,
                bedrooms=2,
            ),
            RawScrapedItem(
                agency_slug="arcari",
                external_id="102",
                ref_code="AR102",
                source_url="https://arcariimoveis.imb.br/102-sobrado-fraron",
                raw_purpose="venda",
                raw_type="Casa",
                title="Sobrado moderno 3 quartos no Fraron",
                raw_city="Pato Branco",
                raw_neighborhood="Fraron",
                sale_price=790000.0,
                area_built_m2=150.0,
                bedrooms=3,
            ),
        ],
        # Same physical apartment listed at a lower rent by Ederson Imóveis (tests cross-agency dedup!)
        "ederson": [
            RawScrapedItem(
                agency_slug="ederson",
                external_id="909",
                ref_code="ED909",
                source_url="https://edersonimoveis.com.br/909-apartamento-centro",
                raw_purpose="aluguel",
                raw_type="Apartamento",
                title="Apartamento 2 dormitórios Centro próximo praça apto 101",
                street="R. Iguaçu",
                street_number="100",
                raw_city="Pato Branco",
                raw_neighborhood="Centro",
                rent_price=2200.0,
                area_private_m2=81.0,
                bedrooms=2,
            ),
        ],
    }

    fake_scraper = FakePlatformScraper(initial_batches)
    sync_service = SyncService(repo, FakeScraperRegistry(fake_scraper), settings)

    # 1. Initial sync of Arcari and Ederson
    report1 = await sync_service.sync_agencies(agency_slugs=("arcari", "ederson"), force=True)
    assert (report1.total_inserted, report1.total_updated) == (3, 0)

    # 2. Immediate non-forced sync skips due to 6h TTL cache
    report_ttl = await sync_service.sync_agencies(agency_slugs=("arcari",), force=False)
    assert report_ttl.agencies[0].status == SyncStatus.SKIPPED_TTL

    # 3. Simulate price drop on #101 + #102 vanishing
    all_page = repo.search_properties(PropertyFilterParams())
    arcari_101 = next(it for it in all_page.items if it.ref_code == "AR101")

    fake_scraper.batches["arcari"] = [
        RawScrapedItem(
            agency_slug="arcari",
            external_id="101",
            ref_code="AR101",
            source_url="https://arcariimoveis.imb.br/101-apto-centro",
            raw_purpose="aluguel",
            raw_type="Apartamento",
            title="Apartamento 2 quartos no Edifício Solar Iguaçu - Centro apto 101",
            street="Rua Iguaçu",
            street_number="100",
            raw_city="Pato Branco",
            raw_neighborhood="Centro",
            rent_price=2250.0,  # Dropped -10% from 2500 -> 2250!
            area_private_m2=80.0,
            bedrooms=2,
        ),
    ]

    report2 = await sync_service.sync_agencies(agency_slugs=("arcari",), force=True)
    assert (report2.total_updated, report2.total_price_changes, report2.total_deactivated) == (1, 1, 1)

    detail_101 = repo.get_property_detail(arcari_101.id)
    assert (
        detail_101.rent_price,
        detail_101.previous_price,
        detail_101.price_delta_pct,
        len(detail_101.price_history),
        len(detail_101.duplicate_peers),
    ) == (2250.0, 2500.0, -10.0, 2, 1)

    # 4. Test FastAPI endpoints + FULLTEXT + deduplication via ASGI client
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        app.state.sync_service = SyncService(
            SqlAlchemyPropertyRepository(create_session_factory(engine)),
            FakeScraperRegistry(fake_scraper),
            settings,
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            health_res = await client.get("/api/health")
            assert health_res.status_code == 200
            assert health_res.json() == {"status": "ok", "city": "Pato Branco - PR"}

            dash_res = await client.get("/")
            assert dash_res.status_code == 200
            assert "Imob Pato Branco" in dash_res.text

            stats_res = await client.get("/api/stats")
            stats_json = stats_res.json()
            assert (
                stats_json["active_properties"],
                stats_json["inactive_properties"],
                stats_json["price_drops_count"],
                stats_json["duplicate_groups_count"],
            ) == (2, 1, 1, 1)
            assert "favorites_count" not in stats_json
            assert "hidden_count" not in stats_json

            # Deduplicated search collapses AR101 (R$ 2250) and ED909 (R$ 2200) to ED909 (lowest price!)
            dedup_res = await client.get("/api/properties", params={"deduplicate": "true"})
            dedup_json = dedup_res.json()
            assert dedup_json["total"] == 1
            assert dedup_json["items"][0]["ref_code"] == "ED909"
            assert dedup_json["items"][0]["duplicate_count"] == 2

            # FULLTEXT query matches "Solar Iguaçu"
            fts_res = await client.get("/api/properties", params={"q": "Solar Iguaçu"})
            fts_json = fts_res.json()
            assert fts_json["total"] == 1
            assert fts_json["items"][0]["ref_code"] == "AR101"

            # Anonymous mutation and scrape triggers no longer exist.
            for method, url in (
                ("POST", "/api/sync"),
                ("POST", f"/api/properties/{arcari_101.id}/enrich"),
                ("PATCH", f"/api/properties/{arcari_101.id}/state"),
                ("PUT", "/api/maps/quadras/1/pdf"),
            ):
                gone = await client.request(method, url, json={"is_favorite": True})
                assert gone.status_code in (404, 405), (method, url, gone.status_code)

            # Detail is the stored catalog snapshot: reading it never scrapes the agency page.
            detail_res = await client.get(f"/api/properties/{arcari_101.id}")
            assert detail_res.status_code == 200
            detail_json = detail_res.json()
            assert detail_json["condo_fee"] is None
            assert detail_json["detail_enriched_at"] is None
            assert not {"is_favorite", "is_hidden", "user_notes"} & set(detail_json)
            assert fake_scraper.detail_calls == 0

            status_res = await client.get("/api/sync/status")
            assert status_res.status_code == 200
            status_json = status_res.json()
            assert status_json["is_running"] is False
            assert status_json["sync_schedule"] == ["06:00", "18:00"]
            assert status_json["sync_timezone"] == "America/Sao_Paulo"
            assert "next_scheduled_run_at" in status_json
            assert "sync_interval_hours" not in status_json
            assert "next_timer_check_at" not in status_json

            await _assert_key_search(client)


async def _assert_key_search(client: httpx.AsyncClient) -> None:
    """Browser favorites/discarded lists filter by stable agency + listing keys only."""
    ar101 = {"agency_slug": "arcari", "external_id": "101"}
    ed909 = {"agency_slug": "ederson", "external_id": "909"}

    async def refs(body: dict[str, object]) -> list[str]:
        res = await client.post("/api/properties/search", json=body)
        assert res.status_code == 200, res.text
        return sorted(item["ref_code"] for item in res.json()["items"])

    assert await refs({}) == ["AR101", "ED909"]
    assert await refs({"include_keys": [ar101]}) == ["AR101"]
    assert await refs({"include_keys": []}) == []
    assert await refs({"exclude_keys": [ar101]}) == ["ED909"]
    # Dedup picks the cheapest listing that is not discarded.
    assert await refs({"exclude_keys": [ed909], "deduplicate": True}) == ["AR101"]
    # A key that matches the external ID but another agency must not hide a different listing.
    assert await refs({"exclude_keys": [{"agency_slug": "ederson", "external_id": "101"}]}) == [
        "AR101",
        "ED909",
    ]
    assert await refs({"include_keys": [ar101], "q": "Solar Iguaçu", "purpose": "aluguel"}) == ["AR101"]

    too_many = [{"agency_slug": "arcari", "external_id": str(i)} for i in range(201)]
    invalid_bodies: list[dict[str, object]] = [
        {"include_keys": too_many},
        {"exclude_keys": too_many * 3},
        {"exclude_keys": [{"agency_slug": "arcari", "external_id": "x" * 97}]},
        {"exclude_keys": [{"agency_slug": "Arcari!", "external_id": "1"}]},
        {"exclude_keys": [{**ar101, "user_notes": "privado"}]},
        {"user_notes": "privado"},
        {"q": "x" * 121},
        {"page_size": 121},
        {"types": ["nao-existe"]},
    ]
    for body in invalid_bodies:
        res = await client.post("/api/properties/search", json=body)
        assert res.status_code == 422, (body, res.status_code)

    assert (await client.get("/api/properties", params={"q": "x" * 121})).status_code == 422
    assert (
        await client.get("/api/properties", params={"neighborhoods": ",".join(f"b{i}" for i in range(201))})
    ).status_code == 422
    get_res = await client.get("/api/properties", params={"only_favorites": "true"})
    assert get_res.status_code == 200
    assert get_res.json()["total"] == 2


@pytest.mark.asyncio
async def test_concurrent_scrapes_serialize_catalog_writes(mysql_db: MySqlTestDatabase) -> None:
    """Keep network scraping concurrent while catalog writes (global match rebuild) run one at a time."""
    engine = mysql_db.engine
    repo = SqlAlchemyPropertyRepository(create_session_factory(engine))
    scraper = FakePlatformScraper(
        {
            slug: [
                RawScrapedItem(
                    agency_slug=slug,
                    external_id="1",
                    source_url=f"https://example.com/{slug}/1",
                    raw_purpose="aluguel",
                    raw_type="Casa",
                    title=f"Casa no Centro {slug}",
                    raw_city="Pato Branco",
                    raw_neighborhood="Centro",
                    rent_price=2000.0,
                )
            ]
            for slug in ("arcari", "ederson")
        }
    )
    sync = SyncService(
        repo,
        FakeScraperRegistry(scraper),
        mysql_db.settings.model_copy(update={"max_concurrent_agencies": 2}),
    )
    original_write = repo.apply_agency_catalog
    guard = threading.Lock()
    active_writes = 0
    peak_writes = 0

    def tracked_write(
        agency: AgencyConfig,
        listings: tuple[NormalizedListing, ...],
        *,
        allow_deactivation: bool,
        sync_status: SyncStatus,
        requests_made: int,
        duration_seconds: float,
        error_message: str | None = None,
    ) -> UpsertBatchOutcome:
        nonlocal active_writes, peak_writes
        with guard:
            active_writes += 1
            peak_writes = max(peak_writes, active_writes)
        try:
            time.sleep(0.05)  # Give another finished scraper time to enter persistence.
            return original_write(
                agency,
                listings,
                allow_deactivation=allow_deactivation,
                sync_status=sync_status,
                requests_made=requests_made,
                duration_seconds=duration_seconds,
                error_message=error_message,
            )
        finally:
            with guard:
                active_writes -= 1

    with patch.object(repo, "apply_agency_catalog", side_effect=tracked_write):
        actual = await sync.sync_agencies(agency_slugs=("arcari", "ederson"), force=True)

    assert actual.total_inserted == 2
    assert peak_writes == 1
    assert repo.get_stats().active_properties == 2


@pytest.mark.asyncio
async def test_circuit_breaker_prevents_mass_deactivation_on_truncated_catalog(
    mysql_db: MySqlTestDatabase,
) -> None:
    """Verify an anomalous >50% catalog drop on a 12-item agency does not wipe active listings."""
    settings = mysql_db.settings
    engine = mysql_db.engine
    repo = SqlAlchemyPropertyRepository(create_session_factory(engine))

    twelve_items = [
        RawScrapedItem(
            agency_slug="solar",
            external_id=str(i),
            ref_code=f"SL{i}",
            source_url=f"https://solar.imb.br/{i}-casa-centro",
            raw_purpose="aluguel",
            raw_type="Casa",
            title=f"Casa {i} no Centro",
            raw_city="Pato Branco",
            raw_neighborhood="Centro",
            rent_price=2000.0 + i * 50,
            area_built_m2=100.0,
            bedrooms=2,
        )
        for i in range(1, 13)
    ]
    fake = FakePlatformScraper({"solar": twelve_items})
    sync = SyncService(repo, FakeScraperRegistry(fake), settings)

    first = await sync.sync_agencies(agency_slugs=("solar",), force=True)
    assert first.total_inserted == 12

    # Simulate transient agency glitch returning only 2 of the 12 listings (<50% of 12)
    fake.batches["solar"] = twelve_items[:2]
    second = await sync.sync_agencies(agency_slugs=("solar",), force=True)

    actual = (second.total_deactivated, repo.get_stats().active_properties)
    expected = (0, 12)
    assert actual == expected

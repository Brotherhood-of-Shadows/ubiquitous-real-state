"""Synchronous persistence checks for identity safety and negotiation enrichment."""

from sqlalchemy import text

from imob.db.engine import create_session_factory
from imob.db.repository import SqlAlchemyPropertyRepository
from imob.domain.enums import SyncStatus
from imob.domain.models import PropertyDetailPatch, PropertyFilterParams, RawScrapedItem
from imob.domain.normalizer import normalize_scraped_item
from imob.domain.registry import AGENCIES_BY_SLUG
from tests.conftest import MySqlTestDatabase


def test_address_identity_and_negotiation_survive_persistence(mysql_db: MySqlTestDatabase) -> None:
    """Check peers, separation, detail patch, and absence of a default price restriction."""
    engine = mysql_db.engine
    repo = SqlAlchemyPropertyRepository(create_session_factory(engine))
    agency = AGENCIES_BY_SLUG["imobilli"]
    repo.seed_agencies((agency,))
    base = RawScrapedItem(
        agency_slug=agency.slug,
        external_id="1",
        source_url="https://example.com/1",
        raw_purpose="aluguel",
        raw_type="Apartamento",
        raw_city="Pato Branco",
        raw_neighborhood="Centro",
        title="Apartamento 2 quartos apto 101",
        area_private_m2=110,
        bedrooms=2,
        street="Rua Tamoio",
        street_number="503",
        description="Aceita permuta e estuda proposta.",
    )
    raw_items = (
        base,
        base.model_copy(update={"external_id": "2"}),
        base.model_copy(update={"external_id": "3", "street": "Rua Tocantins"}),
        base.model_copy(update={"external_id": "4", "street": None}),
    )
    listings = tuple(n for raw in raw_items if (n := normalize_scraped_item(raw, agency)) is not None)
    repo.apply_agency_catalog(
        agency,
        listings,
        allow_deactivation=False,
        sync_status=SyncStatus.SUCCESS,
        requests_made=1,
        duration_seconds=0,
    )
    page = repo.search_properties(PropertyFilterParams())
    assert page.total == 4
    by_ext = {item.external_id: item for item in page.items}
    detail = repo.get_property_detail(by_ext["1"].id)
    assert [peer.id for peer in detail.duplicate_peers] == [by_ext["2"].id]
    assert (detail.accepts_exchange, detail.accepts_proposals) == (True, True)
    assert repo.get_property_detail(by_ext["3"].id).duplicate_peers == ()
    assert repo.get_property_detail(by_ext["4"].id).duplicate_peers == ()
    patched = repo.apply_detail_patch(detail.id, PropertyDetailPatch(accepts_exchange=False))
    assert not patched.accepts_exchange
    assert patched.accepts_proposals
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "0001_mysql_baseline"


def test_existing_groups_rebuild_on_startup_and_after_catalog_change(mysql_db: MySqlTestDatabase) -> None:
    """Rebuild a populated database without scraping and remove stale groups after updates."""
    engine = mysql_db.engine
    repo = SqlAlchemyPropertyRepository(create_session_factory(engine))
    agencies = (AGENCIES_BY_SLUG["imobilli"], AGENCIES_BY_SLUG["famex"])
    repo.seed_agencies(agencies)
    listings = []
    for agency in agencies:
        raw = RawScrapedItem(
            agency_slug=agency.slug,
            external_id="1",
            source_url=f"https://{agency.slug}.example/1",
            raw_purpose="venda",
            raw_type="Apartamento",
            raw_city="Pato Branco",
            raw_neighborhood="Centro",
            title="Apartamento Edifício Andressa",
            sale_price=700000,
            area_private_m2=119,
            bedrooms=3,
            suites=2,
            bathrooms=3,
            parking_spots=1,
        )
        listing = normalize_scraped_item(raw, agency)
        assert listing is not None
        listings.append(listing)
        repo.apply_agency_catalog(
            agency,
            (listing,),
            allow_deactivation=False,
            sync_status=SyncStatus.SUCCESS,
            requests_made=1,
            duration_seconds=0,
        )
    assert repo.get_stats().duplicate_groups_count == 1
    with engine.begin() as conn:
        conn.execute(text("UPDATE properties SET dedup_group_key = NULL"))
    repo.seed_agencies(agencies)
    assert repo.get_stats().duplicate_groups_count == 1
    page = repo.search_properties(PropertyFilterParams(deduplicate=True))
    assert page.total == 1
    detail = repo.get_property_detail(page.items[0].id)
    assert detail.duplicate_count == 2
    assert detail.duplicate_peers[0].match_reasons
    for price in (None, 70000.0, 7000000.0):
        repo.apply_agency_catalog(
            agencies[1],
            (listings[1].model_copy(update={"sale_price": price}),),
            allow_deactivation=False,
            sync_status=SyncStatus.SUCCESS,
            requests_made=1,
            duration_seconds=0,
        )
        assert repo.get_stats().duplicate_groups_count == 1
        assert repo.search_properties(PropertyFilterParams(deduplicate=True)).total == 1
    repo.apply_agency_catalog(
        agencies[1],
        (listings[1].model_copy(update={"bedrooms": 4}),),
        allow_deactivation=False,
        sync_status=SyncStatus.SUCCESS,
        requests_made=1,
        duration_seconds=0,
    )
    assert repo.get_stats().duplicate_groups_count == 0
    assert repo.search_properties(PropertyFilterParams(deduplicate=True)).total == 2


def test_review_pairs_are_queryable_but_never_collapse_results(mysql_db: MySqlTestDatabase) -> None:
    """Store review evidence, expose its filter/details, and drop stale inactive pairs."""
    engine = mysql_db.engine
    repo = SqlAlchemyPropertyRepository(create_session_factory(engine))
    agencies = (AGENCIES_BY_SLUG["imobilli"], AGENCIES_BY_SLUG["famex"])
    repo.seed_agencies(agencies)
    for agency in agencies:
        listing = normalize_scraped_item(
            RawScrapedItem(
                agency_slug=agency.slug,
                external_id="1",
                source_url=f"https://{agency.slug}.example/1",
                raw_purpose="venda",
                raw_type="Apartamento",
                raw_city="Pato Branco",
                raw_neighborhood="Centro",
                title="Apartamento Edifício Andressa",
                bedrooms=3,
            ),
            agency,
        )
        assert listing is not None
        repo.apply_agency_catalog(
            agency,
            (listing,),
            allow_deactivation=False,
            sync_status=SyncStatus.SUCCESS,
            requests_made=1,
            duration_seconds=0,
        )
    stats = repo.get_stats()
    assert (stats.duplicate_groups_count, stats.similarity_review_pairs_count) == (0, 1)
    page = repo.search_properties(PropertyFilterParams(only_similarity_candidates=True, deduplicate=True))
    assert page.total == 2
    detail = repo.get_property_detail(page.items[0].id)
    assert detail.duplicate_peers == ()
    assert detail.similarity_candidates_total == 1
    assert len(detail.similarity_candidates) == 1
    assert detail.similarity_candidates[0].match_reasons
    assert detail.similarity_candidates[0].match_uncertainties
    repo.apply_agency_catalog(
        agencies[1],
        (),
        allow_deactivation=True,
        sync_status=SyncStatus.SUCCESS,
        requests_made=1,
        duration_seconds=0,
    )
    assert repo.get_stats().similarity_review_pairs_count == 0
    assert repo.get_property_detail(detail.id).similarity_candidates == ()
    assert repo.search_properties(PropertyFilterParams(only_similarity_candidates=True)).total == 0

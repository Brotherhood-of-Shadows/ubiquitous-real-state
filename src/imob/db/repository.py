"""SQLAlchemy repository translating persistence rows into immutable domain models."""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, Select, and_, case, delete, false, func, or_, select, tuple_
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.mysql import match as mysql_match
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload, selectinload, sessionmaker

from imob.domain.enums import (
    FurnishedStatus,
    ListingPurpose,
    PlatformKind,
    PropertyType,
    SortOrder,
    SyncStatus,
)
from imob.domain.models import (
    AgencyConfig,
    AgencyStatusView,
    CatalogStats,
    DuplicatePeer,
    FacetBucket,
    ListingKey,
    NormalizedListing,
    PaginatedProperties,
    PriceHistoryEntry,
    PropertyDetail,
    PropertyDetailPatch,
    PropertyFilterParams,
    PropertySummary,
    SearchFacets,
)
from imob.domain.normalizer import sanitize_area, sanitize_fee, slugify
from imob.domain.similarity import (
    SimilarityListing,
    assess_similarity,
    find_similarity_pairs,
    group_similarity_pairs,
)
from imob.errors import EntityNotFoundError, PersistenceError
from imob.ports import UpsertBatchOutcome

from .locks import STARTUP_LOCK_TIMEOUT_SECONDS, MySqlNamedLock
from .orm import FULLTEXT_COLUMNS, AgencyRow, PriceHistoryRow, PropertyMatchRow, PropertyRow, SyncRunRow

_FULLTEXT_SEARCH_COLUMNS = tuple(getattr(PropertyRow, name) for name in FULLTEXT_COLUMNS)
SYNC_LOCK_NAME = "imob_sync"

_PROPERTY_TYPE_LABELS: dict[str, str] = {
    PropertyType.APARTMENT.value: "Apartamento",
    PropertyType.KITNET_STUDIO.value: "Kitnet / Studio / Loft",
    PropertyType.PENTHOUSE.value: "Cobertura",
    PropertyType.HOUSE.value: "Casa",
    PropertyType.SOBRADO.value: "Sobrado",
    PropertyType.CONDO_HOUSE.value: "Casa em Condomínio",
    PropertyType.COMMERCIAL_ROOM.value: "Sala Comercial",
    PropertyType.COMMERCIAL_POINT.value: "Loja / Ponto Comercial",
    PropertyType.WAREHOUSE.value: "Barracão / Galpão",
    PropertyType.BUILDING.value: "Prédio / Edifício",
    PropertyType.LAND.value: "Terreno / Lote",
    PropertyType.RURAL.value: "Chácara / Área Rural",
    PropertyType.OTHER.value: "Outros",
}

_PURPOSE_LABELS: dict[str, str] = {
    ListingPurpose.RENT.value: "Aluguel",
    ListingPurpose.SALE.value: "Venda",
    ListingPurpose.BOTH.value: "Venda e Aluguel",
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


_FTS_OPERATORS = re.compile(r'[+\-<>()~*"@]')
_FTS_MAX_TOKENS = 8
_FTS_MIN_TOKEN_CHARS = 2


def _build_fts_match_expression(raw_query: str) -> str | None:
    """Build a MySQL boolean-mode query requiring every token as a prefix (`+tok* +tok2*`).

    Tokens shorter than 2 characters are dropped (the server indexes tokens of 2+ characters),
    at most 8 tokens are kept, and boolean operators are stripped so user input cannot alter
    the query structure.
    """
    cleaned = _FTS_OPERATORS.sub(" ", raw_query)
    tokens = re.findall(r"\w+", cleaned, flags=re.UNICODE)
    safe_tokens = [tok for tok in tokens if len(tok) >= _FTS_MIN_TOKEN_CHARS][:_FTS_MAX_TOKENS]
    if not safe_tokens:
        return None
    return " ".join(f"+{tok}*" for tok in safe_tokens)


def _fulltext_match(expression: str) -> Any:
    """`MATCH(<ft_properties_search columns>) AGAINST (:expr IN BOOLEAN MODE)`."""
    return mysql_match(*_FULLTEXT_SEARCH_COLUMNS, against=expression).in_boolean_mode()


def _prices_differ(old_val: float | None, new_val: float | None) -> bool:
    if old_val is None and new_val is None:
        return False
    if old_val is None or new_val is None:
        return True
    return abs(old_val - new_val) >= 1.0


class SqlAlchemyPropertyRepository:
    """Concrete MySQL/SQLAlchemy implementation of `PropertyRepositoryProtocol`."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        """Initialize repository with a scoped session factory.

        Args:
            session_factory: SQLAlchemy `sessionmaker` bound to the application database.
        """
        self._session_factory = session_factory

    def _engine(self) -> Engine:
        with self._session_factory() as session:
            bind = session.get_bind()
        return bind if isinstance(bind, Engine) else bind.engine

    def try_acquire_sync_lock(self) -> MySqlNamedLock | None:
        """Take the cross-process sync lock without waiting.

        Returns:
            The held lock (call `release()` when done), or `None` when another process holds it.

        Raises:
            PersistenceError: When the database cannot be reached.
        """
        lock = MySqlNamedLock(self._engine(), SYNC_LOCK_NAME)
        try:
            acquired = lock.try_acquire()
        except SQLAlchemyError as exc:
            raise PersistenceError(f"Failed to acquire the sync lock: {type(exc).__name__}") from exc
        return lock if acquired else None

    def seed_agencies(self, agencies: tuple[AgencyConfig, ...], *, sync_lock_held: bool = False) -> None:
        """Upsert all registered agencies into `agencies` and rebuild similarity groups under the sync lock.

        Args:
            agencies: Registered agency configurations.
            sync_lock_held: Set `True` when the caller already holds the database-scoped `imob_sync` lock.

        Raises:
            PersistenceError: When the sync lock times out or the database write fails.
        """
        if not agencies:
            return
        records = [
            dict(slug=cfg.slug, name=cfg.name, base_url=cfg.base_url, platform=cfg.platform.value)
            for cfg in sorted(agencies, key=lambda agency: agency.slug)
        ]
        stmt = mysql_insert(AgencyRow).values(records)
        lock: MySqlNamedLock | None = None
        if not sync_lock_held:
            lock = MySqlNamedLock(self._engine(), SYNC_LOCK_NAME)
            try:
                acquired = lock.try_acquire(STARTUP_LOCK_TIMEOUT_SECONDS)
            except SQLAlchemyError as exc:
                raise PersistenceError(f"Failed to acquire the sync lock: {type(exc).__name__}") from exc
            if not acquired:
                raise PersistenceError("Timed out waiting for the sync lock while seeding agencies.")
        try:
            with self._session_factory() as session, session.begin():
                session.execute(
                    stmt.on_duplicate_key_update(
                        name=stmt.inserted.name,
                        base_url=stmt.inserted.base_url,
                        platform=stmt.inserted.platform,
                    )
                )
                _rebuild_similarity_groups(session)
        except SQLAlchemyError as exc:
            raise PersistenceError(f"Failed to seed agencies: {exc}") from exc
        finally:
            if lock is not None:
                lock.release()

    def list_agencies(self) -> tuple[AgencyStatusView, ...]:
        """List all agencies alongside active/total property counts and last sync status.

        Returns:
            Tuple of `AgencyStatusView` sorted by name.

        Raises:
            PersistenceError: When query fails.
        """
        try:
            with self._session_factory() as session:
                counts_subq = (
                    select(
                        PropertyRow.agency_slug,
                        func.count(PropertyRow.id).label("total_cnt"),
                        func.sum(case((PropertyRow.is_active.is_(True), 1), else_=0)).label("active_cnt"),
                    )
                    .group_by(PropertyRow.agency_slug)
                    .subquery()
                )
                stmt = (
                    select(
                        AgencyRow,
                        func.coalesce(counts_subq.c.active_cnt, 0),
                        func.coalesce(counts_subq.c.total_cnt, 0),
                    )
                    .outerjoin(counts_subq, AgencyRow.slug == counts_subq.c.agency_slug)
                    .order_by(AgencyRow.name.asc())
                )
                result: list[AgencyStatusView] = []
                for agency_row, active_cnt, total_cnt in session.execute(stmt).all():
                    result.append(
                        AgencyStatusView(
                            slug=agency_row.slug,
                            name=agency_row.name,
                            base_url=agency_row.base_url,
                            platform=PlatformKind(agency_row.platform),
                            last_synced_at=_ensure_utc(agency_row.last_synced_at),
                            last_sync_status=(
                                SyncStatus(agency_row.last_sync_status)
                                if agency_row.last_sync_status
                                else None
                            ),
                            last_error=agency_row.last_error,
                            active_properties=int(active_cnt or 0),
                            total_properties=int(total_cnt or 0),
                        )
                    )
                return tuple(result)
        except SQLAlchemyError as exc:
            raise PersistenceError(f"Failed to list agencies: {exc}") from exc

    def apply_agency_catalog(
        self,
        agency: AgencyConfig,
        listings: tuple[NormalizedListing, ...],
        *,
        allow_deactivation: bool,
        sync_status: SyncStatus,
        requests_made: int,
        duration_seconds: float,
        error_message: str | None = None,
    ) -> UpsertBatchOutcome:
        """Persist synchronized agency listings, record price changes, and deactivate vanished items.

        Args:
            agency: Source agency configuration.
            listings: Deduplicated normalized listings scraped in the current run.
            allow_deactivation: Permit marking unseen active rows as `is_active = False`.
            sync_status: Run status (`success` or `partial`).
            requests_made: Number of HTTP requests performed.
            duration_seconds: Wall-clock duration of the scrape.
            error_message: Optional warning/partial error description.

        Returns:
            `UpsertBatchOutcome` metrics.

        Raises:
            PersistenceError: When transaction commit fails.
        """
        now = _utc_now()
        started_at = now - timedelta(seconds=max(0.0, duration_seconds))
        # Deduplicate incoming listings by external_id (merge when in both venda and locacao tabs)
        merged_by_ext = _merge_same_agency_listings(listings)

        inserted = 0
        updated = 0
        price_changes = 0
        deactivated = 0

        try:
            with self._session_factory() as session, session.begin():
                agency_row = session.get(AgencyRow, agency.slug)
                if agency_row is None:
                    agency_row = AgencyRow(
                        slug=agency.slug,
                        name=agency.name,
                        base_url=agency.base_url,
                        platform=agency.platform.value,
                    )
                    session.add(agency_row)

                existing_rows = {
                    row.external_id: row
                    for row in session.scalars(
                        select(PropertyRow).where(PropertyRow.agency_slug == agency.slug)
                    ).all()
                }

                seen_ext_ids: set[str] = set()
                new_rows_with_initial_price: list[PropertyRow] = []

                for ext_id, item in merged_by_ext.items():
                    seen_ext_ids.add(ext_id)
                    existing = existing_rows.get(ext_id)
                    if existing is None:
                        prev_p = item.previous_listed_price
                        curr_p = (
                            item.sale_price
                            if item.purpose == ListingPurpose.SALE
                            else (item.rent_price or item.sale_price)
                        )
                        delta_pct = (
                            round(((curr_p - prev_p) / prev_p) * 100.0, 2)
                            if prev_p and curr_p and prev_p > 0 and abs(curr_p - prev_p) >= 1.0
                            else None
                        )
                        row = PropertyRow(
                            agency_slug=agency.slug,
                            external_id=item.external_id,
                            ref_code=item.ref_code,
                            source_url=item.source_url,
                            whatsapp_url=item.whatsapp_url,
                            purpose=item.purpose.value,
                            property_type=item.property_type.value,
                            raw_type=item.raw_type,
                            raw_purpose=item.raw_purpose,
                            type_reconciled=item.type_reconciled,
                            title=item.title,
                            description=item.description,
                            city=item.city,
                            state=item.state,
                            neighborhood=item.neighborhood,
                            neighborhood_slug=item.neighborhood_slug,
                            street=item.street,
                            street_number=item.street_number,
                            postal_code=item.postal_code,
                            building_name=item.building_name,
                            sale_price=item.sale_price,
                            rent_price=item.rent_price,
                            condo_fee=item.condo_fee,
                            iptu_fee=item.iptu_fee,
                            insurance_fee=item.insurance_fee,
                            effective_monthly_total=item.effective_monthly_total,
                            price_per_m2=item.price_per_m2,
                            price_on_request=item.price_on_request,
                            previous_price=prev_p if delta_pct is not None else None,
                            price_delta_pct=delta_pct,
                            area_private_m2=item.area_private_m2,
                            area_built_m2=item.area_built_m2,
                            area_total_m2=item.area_total_m2,
                            usable_area_m2=item.usable_area_m2,
                            bedrooms=item.bedrooms,
                            suites=item.suites,
                            bathrooms=item.bathrooms,
                            parking_spots=item.parking_spots,
                            furnished_status=item.furnished_status.value,
                            accepts_pets=item.accepts_pets,
                            has_elevator=item.has_elevator,
                            has_pool=item.has_pool,
                            has_barbecue=item.has_barbecue,
                            has_balcony=item.has_balcony,
                            has_air_conditioning=item.has_air_conditioning,
                            has_solar=item.has_solar,
                            has_yard=item.has_yard,
                            accepts_financing=item.accepts_financing,
                            accepts_exchange=item.accepts_exchange,
                            accepts_proposals=item.accepts_proposals,
                            latitude=item.latitude,
                            longitude=item.longitude,
                            cover_image_url=item.cover_image_url,
                            image_urls_json=json.dumps(list(item.image_urls)),
                            image_count=len(item.image_urls),
                            dedup_group_key=None,
                            fingerprint_hash=item.fingerprint_hash,
                            is_active=True,
                            detail_enriched_at=now if item.detail_complete else None,
                            first_seen_at=now,
                            last_seen_at=now,
                        )
                        session.add(row)
                        new_rows_with_initial_price.append(row)
                        inserted += 1
                    else:
                        price_changed = _prices_differ(
                            existing.sale_price, item.sale_price
                        ) or _prices_differ(existing.rent_price, item.rent_price)
                        old_ref_price = (
                            existing.sale_price
                            if existing.purpose == ListingPurpose.SALE.value
                            else (existing.rent_price or existing.sale_price)
                        )
                        new_ref_price = (
                            item.sale_price
                            if item.purpose == ListingPurpose.SALE
                            else (item.rent_price or item.sale_price)
                        )

                        # Preserve previously enriched fields when fast catalog pass omits them
                        merged_condo = item.condo_fee if item.condo_fee is not None else existing.condo_fee
                        merged_iptu = item.iptu_fee if item.iptu_fee is not None else existing.iptu_fee
                        merged_ins = (
                            item.insurance_fee if item.insurance_fee is not None else existing.insurance_fee
                        )
                        merged_monthly: float | None = None
                        if item.rent_price is not None:
                            merged_monthly = round(
                                item.rent_price
                                + (merged_condo or 0.0)
                                + (merged_iptu or 0.0)
                                + (merged_ins or 0.0),
                                2,
                            )

                        if price_changed:
                            if (
                                old_ref_price is not None
                                and new_ref_price is not None
                                and old_ref_price > 0
                            ):
                                existing.previous_price = old_ref_price
                                existing.price_delta_pct = round(
                                    ((new_ref_price - old_ref_price) / old_ref_price) * 100.0,
                                    2,
                                )
                            session.add(
                                PriceHistoryRow(
                                    property_id=existing.id,
                                    recorded_at=now,
                                    sale_price=item.sale_price,
                                    rent_price=item.rent_price,
                                    condo_fee=merged_condo,
                                    iptu_fee=merged_iptu,
                                    effective_monthly_total=merged_monthly,
                                )
                            )
                            price_changes += 1

                        was_inactive = not existing.is_active
                        fp_changed = existing.fingerprint_hash != item.fingerprint_hash

                        existing.source_url = item.source_url
                        existing.ref_code = item.ref_code or existing.ref_code
                        existing.whatsapp_url = item.whatsapp_url or existing.whatsapp_url
                        existing.purpose = item.purpose.value
                        existing.property_type = item.property_type.value
                        existing.raw_type = item.raw_type
                        existing.raw_purpose = item.raw_purpose
                        existing.type_reconciled = item.type_reconciled
                        existing.title = item.title
                        if item.description:
                            existing.description = item.description
                        existing.neighborhood = item.neighborhood or existing.neighborhood
                        existing.neighborhood_slug = item.neighborhood_slug or existing.neighborhood_slug
                        existing.street = item.street or existing.street
                        existing.street_number = item.street_number or existing.street_number
                        existing.postal_code = item.postal_code or existing.postal_code
                        existing.building_name = item.building_name or existing.building_name
                        existing.sale_price = item.sale_price
                        existing.rent_price = item.rent_price
                        existing.condo_fee = merged_condo
                        existing.iptu_fee = merged_iptu
                        existing.insurance_fee = merged_ins
                        existing.effective_monthly_total = merged_monthly
                        existing.price_on_request = item.price_on_request
                        existing.area_private_m2 = item.area_private_m2 or existing.area_private_m2
                        existing.area_built_m2 = item.area_built_m2 or existing.area_built_m2
                        existing.area_total_m2 = item.area_total_m2 or existing.area_total_m2
                        existing.usable_area_m2 = item.usable_area_m2 or existing.usable_area_m2
                        if (
                            new_ref_price is not None
                            and existing.usable_area_m2
                            and existing.usable_area_m2 >= 10.0
                        ):
                            existing.price_per_m2 = round(new_ref_price / existing.usable_area_m2, 2)
                        else:
                            existing.price_per_m2 = item.price_per_m2
                        existing.bedrooms = (
                            item.bedrooms if item.bedrooms is not None else existing.bedrooms
                        )
                        existing.suites = item.suites if item.suites is not None else existing.suites
                        existing.bathrooms = (
                            item.bathrooms if item.bathrooms is not None else existing.bathrooms
                        )
                        existing.parking_spots = (
                            item.parking_spots if item.parking_spots is not None else existing.parking_spots
                        )
                        if item.furnished_status != FurnishedStatus.UNKNOWN:
                            existing.furnished_status = item.furnished_status.value
                        existing.accepts_pets = existing.accepts_pets or item.accepts_pets
                        existing.has_elevator = existing.has_elevator or item.has_elevator
                        existing.has_pool = existing.has_pool or item.has_pool
                        existing.has_barbecue = existing.has_barbecue or item.has_barbecue
                        existing.has_balcony = existing.has_balcony or item.has_balcony
                        existing.has_air_conditioning = (
                            existing.has_air_conditioning or item.has_air_conditioning
                        )
                        existing.has_solar = existing.has_solar or item.has_solar
                        existing.has_yard = existing.has_yard or item.has_yard
                        existing.accepts_financing = existing.accepts_financing or item.accepts_financing
                        existing.accepts_exchange = existing.accepts_exchange or item.accepts_exchange
                        existing.accepts_proposals = existing.accepts_proposals or item.accepts_proposals
                        existing.latitude = (
                            item.latitude if item.latitude is not None else existing.latitude
                        )
                        existing.longitude = (
                            item.longitude if item.longitude is not None else existing.longitude
                        )

                        if len(item.image_urls) >= existing.image_count and item.image_urls:
                            existing.cover_image_url = item.cover_image_url
                            existing.image_urls_json = json.dumps(list(item.image_urls))
                            existing.image_count = len(item.image_urls)

                        existing.fingerprint_hash = item.fingerprint_hash
                        existing.is_active = True
                        existing.last_seen_at = now
                        if item.detail_complete:
                            existing.detail_enriched_at = now

                        if price_changed or was_inactive or fp_changed:
                            updated += 1

                session.flush()
                for row in new_rows_with_initial_price:
                    if row.sale_price is not None or row.rent_price is not None:
                        session.add(
                            PriceHistoryRow(
                                property_id=row.id,
                                recorded_at=now,
                                sale_price=row.sale_price,
                                rent_price=row.rent_price,
                                condo_fee=row.condo_fee,
                                iptu_fee=row.iptu_fee,
                                effective_monthly_total=row.effective_monthly_total,
                            )
                        )

                # Deactivate previously active rows that disappeared from a healthy full catalog scan
                if allow_deactivation:
                    currently_active_rows = [r for r in existing_rows.values() if r.is_active]
                    missing_rows = [r for r in currently_active_rows if r.external_id not in seen_ext_ids]
                    # Circuit-breaker: skip deactivation if < 35% of >= 10 active rows returned
                    safe_to_deactivate = True
                    if len(currently_active_rows) >= 10 and len(seen_ext_ids) < max(
                        3, int(len(currently_active_rows) * 0.35)
                    ):
                        safe_to_deactivate = False
                    if safe_to_deactivate:
                        for vanished in missing_rows:
                            vanished.is_active = False
                            deactivated += 1

                agency_row.last_synced_at = now
                agency_row.last_sync_status = sync_status.value
                agency_row.last_error = error_message

                session.add(
                    SyncRunRow(
                        agency_slug=agency.slug,
                        started_at=started_at,
                        finished_at=now,
                        status=sync_status.value,
                        requests_made=requests_made,
                        inserted_count=inserted,
                        updated_count=updated,
                        price_changes_count=price_changes,
                        deactivated_count=deactivated,
                        error_message=error_message,
                    )
                )

                session.flush()
                _rebuild_similarity_groups(session)

            return UpsertBatchOutcome(
                inserted=inserted,
                updated=updated,
                price_changes=price_changes,
                deactivated=deactivated,
            )
        except SQLAlchemyError as exc:
            raise PersistenceError(f"Failed to persist catalog for '{agency.slug}': {exc}") from exc

    def record_failed_sync(
        self,
        agency: AgencyConfig,
        *,
        requests_made: int,
        duration_seconds: float,
        error_message: str,
    ) -> None:
        """Record a failed sync run while preserving all active listings untouched.

        Args:
            agency: Target agency configuration.
            requests_made: HTTP requests attempted.
            duration_seconds: Wall-clock duration.
            error_message: Diagnostic summary.

        Raises:
            PersistenceError: When writing the sync audit row fails.
        """
        now = _utc_now()
        started_at = now - timedelta(seconds=max(0.0, duration_seconds))
        try:
            with self._session_factory() as session, session.begin():
                agency_row = session.get(AgencyRow, agency.slug)
                if agency_row is not None:
                    agency_row.last_sync_status = SyncStatus.FAILED.value
                    agency_row.last_error = error_message[:1000]
                session.add(
                    SyncRunRow(
                        agency_slug=agency.slug,
                        started_at=started_at,
                        finished_at=now,
                        status=SyncStatus.FAILED.value,
                        requests_made=requests_made,
                        inserted_count=0,
                        updated_count=0,
                        price_changes_count=0,
                        deactivated_count=0,
                        error_message=error_message[:1000],
                    )
                )
        except SQLAlchemyError as exc:
            raise PersistenceError(f"Failed to record failed sync for '{agency.slug}': {exc}") from exc

    def search_properties(self, filters: PropertyFilterParams) -> PaginatedProperties:
        """Execute a rich multi-filter property search with FULLTEXT, sorting, and deduplication stats.

        Args:
            filters: Validated search filter parameters.

        Returns:
            `PaginatedProperties` page.

        Raises:
            PersistenceError: When database read fails.
        """
        try:
            with self._session_factory() as session:
                price_expr = _effective_price_column(filters.purpose)

                dedup_stats = (
                    select(
                        PropertyRow.dedup_group_key.label("dg_key"),
                        func.count(func.distinct(PropertyRow.agency_slug)).label("agency_cnt"),
                        func.min(func.coalesce(PropertyRow.rent_price, PropertyRow.sale_price)).label(
                            "min_p"
                        ),
                    )
                    .where(PropertyRow.is_active.is_(True), PropertyRow.dedup_group_key.is_not(None))
                    .group_by(PropertyRow.dedup_group_key)
                    .subquery()
                )

                conditions = _build_filter_conditions(filters, price_expr)

                if filters.deduplicate:
                    group_partition = func.coalesce(
                        PropertyRow.dedup_group_key,
                        func.concat("unique-", PropertyRow.id),
                    )
                    rn_col = (
                        func.row_number()
                        .over(
                            partition_by=group_partition,
                            order_by=(
                                case((price_expr.is_(None), 1), else_=0).asc(),
                                price_expr.asc(),
                                PropertyRow.id.asc(),
                            ),
                        )
                        .label("dedup_rn")
                    )
                    ranked_ids = (
                        select(PropertyRow.id.label("prop_id"), rn_col).where(*conditions).subquery()
                    )
                    base_ids_filter = PropertyRow.id.in_(
                        select(ranked_ids.c.prop_id).where(ranked_ids.c.dedup_rn == 1)
                    )
                    count_stmt: Select[Any] = select(func.count(PropertyRow.id)).where(base_ids_filter)
                    where_clauses: list[Any] = [base_ids_filter]
                else:
                    count_stmt = select(func.count(PropertyRow.id)).where(*conditions)
                    where_clauses = list(conditions)

                total = int(session.scalar(count_stmt) or 0)
                total_pages = max(1, math.ceil(total / filters.page_size)) if total > 0 else 1
                offset = (filters.page - 1) * filters.page_size

                order_clauses = _build_sort_clauses(filters.sort, price_expr)
                query = (
                    select(
                        PropertyRow,
                        AgencyRow.name,
                        func.coalesce(dedup_stats.c.agency_cnt, 1),
                        dedup_stats.c.min_p,
                    )
                    .join(AgencyRow, PropertyRow.agency_slug == AgencyRow.slug)
                    .outerjoin(dedup_stats, PropertyRow.dedup_group_key == dedup_stats.c.dg_key)
                    .where(*where_clauses)
                    .order_by(*order_clauses)
                    .offset(offset)
                    .limit(filters.page_size)
                )

                items = tuple(
                    _to_property_summary(prop_row, agency_name, int(dup_cnt or 1), min_p)
                    for prop_row, agency_name, dup_cnt, min_p in session.execute(query).all()
                )
                return PaginatedProperties(
                    total=total,
                    page=filters.page,
                    page_size=filters.page_size,
                    total_pages=total_pages,
                    items=items,
                )
        except SQLAlchemyError as exc:
            raise PersistenceError(f"Property search failed: {exc}") from exc

    def get_property_detail(self, property_id: int) -> PropertyDetail:
        """Load full property detail including price history and cross-agency peers.

        Args:
            property_id: Internal property ID.

        Returns:
            `PropertyDetail` record.

        Raises:
            EntityNotFoundError: When `property_id` does not exist.
            PersistenceError: When query fails.
        """
        try:
            with self._session_factory() as session:
                stmt = (
                    select(PropertyRow)
                    .options(joinedload(PropertyRow.agency), selectinload(PropertyRow.price_history))
                    .where(PropertyRow.id == property_id)
                )
                row = session.scalar(stmt)
                if row is None:
                    raise EntityNotFoundError("Property", property_id)
                return _build_detail_with_peers(session, row)
        except SQLAlchemyError as exc:
            raise PersistenceError(f"Failed to load property {property_id}: {exc}") from exc

    def apply_detail_patch(self, property_id: int, patch: PropertyDetailPatch) -> PropertyDetail:
        """Merge supplemental detail fields scraped from a property page into `property_id`.

        Args:
            property_id: Internal property ID.
            patch: Enriched property fields.

        Returns:
            Updated `PropertyDetail`.

        Raises:
            EntityNotFoundError: When `property_id` does not exist.
            PersistenceError: When database update fails.
        """
        now = _utc_now()
        try:
            with self._session_factory() as session, session.begin():
                row = session.scalar(
                    select(PropertyRow)
                    .options(joinedload(PropertyRow.agency), selectinload(PropertyRow.price_history))
                    .where(PropertyRow.id == property_id)
                )
                if row is None:
                    raise EntityNotFoundError("Property", property_id)

                if patch.description:
                    row.description = patch.description.strip()
                if patch.condo_fee is not None:
                    row.condo_fee = sanitize_fee(patch.condo_fee)
                if patch.iptu_fee is not None:
                    row.iptu_fee = sanitize_fee(patch.iptu_fee)
                if patch.area_private_m2 is not None:
                    row.area_private_m2 = sanitize_area(patch.area_private_m2) or row.area_private_m2
                if patch.area_built_m2 is not None:
                    row.area_built_m2 = sanitize_area(patch.area_built_m2) or row.area_built_m2
                if patch.area_total_m2 is not None:
                    row.area_total_m2 = sanitize_area(patch.area_total_m2) or row.area_total_m2

                row.usable_area_m2 = (
                    row.area_total_m2
                    if row.property_type in (PropertyType.LAND.value, PropertyType.RURAL.value)
                    and row.area_total_m2 is not None
                    else (row.area_private_m2 or row.area_built_m2 or row.area_total_m2)
                )

                if row.rent_price is not None:
                    row.effective_monthly_total = round(
                        row.rent_price
                        + (row.condo_fee or 0.0)
                        + (row.iptu_fee or 0.0)
                        + (row.insurance_fee or 0.0),
                        2,
                    )

                ref_p = (
                    row.sale_price
                    if row.purpose == ListingPurpose.SALE.value
                    else (row.rent_price or row.sale_price)
                )
                if ref_p is not None and row.usable_area_m2 and row.usable_area_m2 >= 10.0:
                    row.price_per_m2 = round(ref_p / row.usable_area_m2, 2)

                if patch.bedrooms is not None and row.bedrooms is None:
                    row.bedrooms = patch.bedrooms
                if patch.suites is not None and row.suites is None:
                    row.suites = patch.suites
                if patch.bathrooms is not None and row.bathrooms is None:
                    row.bathrooms = patch.bathrooms
                if patch.parking_spots is not None and row.parking_spots is None:
                    row.parking_spots = patch.parking_spots
                if patch.neighborhood and not row.neighborhood:
                    row.neighborhood = patch.neighborhood
                    row.neighborhood_slug = slugify(patch.neighborhood)
                if patch.street and not row.street:
                    row.street = patch.street[:180]
                if patch.latitude is not None and row.latitude is None:
                    row.latitude = patch.latitude
                if patch.longitude is not None and row.longitude is None:
                    row.longitude = patch.longitude
                if patch.furnished_status and patch.furnished_status != FurnishedStatus.UNKNOWN:
                    row.furnished_status = patch.furnished_status.value
                if patch.accepts_pets:
                    row.accepts_pets = True
                if patch.has_elevator:
                    row.has_elevator = True
                if patch.has_pool:
                    row.has_pool = True
                if patch.has_barbecue:
                    row.has_barbecue = True
                if patch.has_balcony:
                    row.has_balcony = True
                if patch.has_air_conditioning:
                    row.has_air_conditioning = True
                if patch.has_solar:
                    row.has_solar = True
                if patch.has_yard:
                    row.has_yard = True
                if patch.accepts_financing:
                    row.accepts_financing = True
                if patch.accepts_exchange is not None:
                    row.accepts_exchange = patch.accepts_exchange
                if patch.accepts_proposals is not None:
                    row.accepts_proposals = patch.accepts_proposals

                if patch.image_urls:
                    existing_imgs = _decode_image_list(row.image_urls_json)
                    combined_imgs = list(dict.fromkeys([*existing_imgs, *patch.image_urls]))
                    row.image_urls_json = json.dumps(combined_imgs)
                    row.image_count = len(combined_imgs)
                    if not row.cover_image_url and combined_imgs:
                        row.cover_image_url = combined_imgs[0]

                row.detail_enriched_at = now
                session.flush()
                _rebuild_similarity_groups(session)
                session.flush()
                return _build_detail_with_peers(session, row)
        except SQLAlchemyError as exc:
            raise PersistenceError(f"Failed to enrich property {property_id}: {exc}") from exc

    def get_facets(self, purpose: str | None = None) -> SearchFacets:
        """Compute dynamic filter facet counts across active listings.

        Args:
            purpose: Optional purpose (`aluguel` or `venda`) to scope counts.

        Returns:
            `SearchFacets` buckets.

        Raises:
            PersistenceError: When query fails.
        """
        try:
            with self._session_factory() as session:
                base_conds: list[Any] = [PropertyRow.is_active.is_(True)]
                if purpose in (ListingPurpose.RENT.value, ListingPurpose.SALE.value):
                    base_conds.append(
                        or_(
                            PropertyRow.purpose == purpose, PropertyRow.purpose == ListingPurpose.BOTH.value
                        )
                    )

                types_rows = session.execute(
                    select(PropertyRow.property_type, func.count(PropertyRow.id))
                    .where(*base_conds)
                    .group_by(PropertyRow.property_type)
                    .order_by(func.count(PropertyRow.id).desc())
                ).all()
                type_buckets = tuple(
                    FacetBucket(
                        value=t_val,
                        label=_PROPERTY_TYPE_LABELS.get(t_val, t_val),
                        count=int(cnt),
                    )
                    for t_val, cnt in types_rows
                )

                nb_rows = session.execute(
                    select(
                        PropertyRow.neighborhood_slug, PropertyRow.neighborhood, func.count(PropertyRow.id)
                    )
                    .where(*base_conds, PropertyRow.neighborhood_slug.is_not(None))
                    .group_by(PropertyRow.neighborhood_slug, PropertyRow.neighborhood)
                    .order_by(func.count(PropertyRow.id).desc(), PropertyRow.neighborhood.asc())
                ).all()
                nb_buckets = tuple(
                    FacetBucket(value=slug, label=label or slug, count=int(cnt))
                    for slug, label, cnt in nb_rows
                    if slug
                )

                ag_rows = session.execute(
                    select(AgencyRow.slug, AgencyRow.name, func.count(PropertyRow.id))
                    .join(PropertyRow, PropertyRow.agency_slug == AgencyRow.slug)
                    .where(*base_conds)
                    .group_by(AgencyRow.slug, AgencyRow.name)
                    .order_by(func.count(PropertyRow.id).desc(), AgencyRow.name.asc())
                ).all()
                ag_buckets = tuple(
                    FacetBucket(value=slug, label=name, count=int(cnt)) for slug, name, cnt in ag_rows
                )

                p_rows = session.execute(
                    select(PropertyRow.purpose, func.count(PropertyRow.id))
                    .where(PropertyRow.is_active.is_(True))
                    .group_by(PropertyRow.purpose)
                ).all()
                p_buckets = tuple(
                    FacetBucket(value=p_val, label=_PURPOSE_LABELS.get(p_val, p_val), count=int(cnt))
                    for p_val, cnt in p_rows
                )

                return SearchFacets(
                    property_types=type_buckets,
                    neighborhoods=nb_buckets,
                    agencies=ag_buckets,
                    purposes=p_buckets,
                )
        except SQLAlchemyError as exc:
            raise PersistenceError(f"Failed to compute facets: {exc}") from exc

    def get_stats(self) -> CatalogStats:
        """Compute aggregate catalog statistics for Pato Branco.

        Returns:
            `CatalogStats` snapshot.

        Raises:
            PersistenceError: When query fails.
        """
        try:
            with self._session_factory() as session:
                active = int(
                    session.scalar(
                        select(func.count(PropertyRow.id)).where(PropertyRow.is_active.is_(True))
                    )
                    or 0
                )
                inactive = int(
                    session.scalar(
                        select(func.count(PropertyRow.id)).where(PropertyRow.is_active.is_(False))
                    )
                    or 0
                )
                rent_cnt = int(
                    session.scalar(
                        select(func.count(PropertyRow.id)).where(
                            PropertyRow.is_active.is_(True),
                            PropertyRow.purpose.in_([ListingPurpose.RENT.value, ListingPurpose.BOTH.value]),
                        )
                    )
                    or 0
                )
                sale_cnt = int(
                    session.scalar(
                        select(func.count(PropertyRow.id)).where(
                            PropertyRow.is_active.is_(True),
                            PropertyRow.purpose.in_([ListingPurpose.SALE.value, ListingPurpose.BOTH.value]),
                        )
                    )
                    or 0
                )
                both_cnt = int(
                    session.scalar(
                        select(func.count(PropertyRow.id)).where(
                            PropertyRow.is_active.is_(True),
                            PropertyRow.purpose == ListingPurpose.BOTH.value,
                        )
                    )
                    or 0
                )
                ag_total = int(session.scalar(select(func.count(AgencyRow.slug))) or 0)
                ag_synced = int(
                    session.scalar(
                        select(func.count(AgencyRow.slug)).where(AgencyRow.last_synced_at.is_not(None))
                    )
                    or 0
                )
                drops_cnt = int(
                    session.scalar(
                        select(func.count(PropertyRow.id)).where(
                            PropertyRow.is_active.is_(True),
                            PropertyRow.price_delta_pct.is_not(None),
                            PropertyRow.price_delta_pct < 0,
                        )
                    )
                    or 0
                )
                dup_subq = (
                    select(PropertyRow.dedup_group_key)
                    .where(PropertyRow.is_active.is_(True), PropertyRow.dedup_group_key.is_not(None))
                    .group_by(PropertyRow.dedup_group_key)
                    .having(func.count(func.distinct(PropertyRow.agency_slug)) > 1)
                    .subquery()
                )
                dup_groups = int(session.scalar(select(func.count()).select_from(dup_subq)) or 0)
                last_sync = session.scalar(select(func.max(AgencyRow.last_synced_at)))

                return CatalogStats(
                    active_properties=active,
                    inactive_properties=inactive,
                    rent_count=rent_cnt,
                    sale_count=sale_cnt,
                    both_count=both_cnt,
                    agencies_total=ag_total,
                    agencies_synced=ag_synced,
                    price_drops_count=drops_cnt,
                    duplicate_groups_count=dup_groups,
                    similarity_review_pairs_count=int(
                        session.scalar(
                            select(func.count())
                            .select_from(PropertyMatchRow)
                            .where(PropertyMatchRow.level == "review")
                        )
                        or 0
                    ),
                    last_global_sync_at=_ensure_utc(last_sync),
                )
        except SQLAlchemyError as exc:
            raise PersistenceError(f"Failed to compute catalog stats: {exc}") from exc


def _merge_same_agency_listings(listings: tuple[NormalizedListing, ...]) -> dict[str, NormalizedListing]:
    """Merge duplicate external_ids within the same agency (e.g. listed in both rent and sale tabs)."""
    by_ext: dict[str, NormalizedListing] = {}
    for item in listings:
        prev = by_ext.get(item.external_id)
        if prev is None:
            by_ext[item.external_id] = item
            continue
        sale_p = item.sale_price if item.sale_price is not None else prev.sale_price
        rent_p = item.rent_price if item.rent_price is not None else prev.rent_price
        purpose = (
            ListingPurpose.BOTH
            if (prev.purpose != item.purpose or (sale_p is not None and rent_p is not None))
            else item.purpose
        )
        monthly = item.effective_monthly_total or prev.effective_monthly_total
        combined_imgs = tuple(dict.fromkeys([*prev.image_urls, *item.image_urls]))
        by_ext[item.external_id] = item.model_copy(
            update={
                "purpose": purpose,
                "sale_price": sale_p,
                "rent_price": rent_p,
                "effective_monthly_total": monthly,
                "price_on_request": (sale_p is None and rent_p is None),
                "image_urls": combined_imgs,
                "cover_image_url": combined_imgs[0] if combined_imgs else None,
            }
        )
    return by_ext


def _effective_price_column(purpose: ListingPurpose | None) -> Any:
    if purpose == ListingPurpose.RENT:
        return PropertyRow.rent_price
    if purpose == ListingPurpose.SALE:
        return PropertyRow.sale_price
    return func.coalesce(PropertyRow.rent_price, PropertyRow.sale_price)


def _listing_key_column() -> Any:
    return tuple_(PropertyRow.agency_slug, PropertyRow.external_id)


def _listing_key_values(keys: tuple[ListingKey, ...]) -> list[tuple[str, str]]:
    return list(dict.fromkeys((key.agency_slug, key.external_id) for key in keys))


def _build_filter_conditions(filters: PropertyFilterParams, price_expr: Any) -> list[Any]:
    conds: list[Any] = []
    if not filters.include_inactive:
        conds.append(PropertyRow.is_active.is_(True))
    if filters.include_keys is not None:
        if filters.include_keys:
            conds.append(_listing_key_column().in_(_listing_key_values(filters.include_keys)))
        else:
            conds.append(false())
    if filters.exclude_keys:
        conds.append(_listing_key_column().not_in(_listing_key_values(filters.exclude_keys)))

    if filters.purpose == ListingPurpose.RENT:
        conds.append(PropertyRow.purpose.in_([ListingPurpose.RENT.value, ListingPurpose.BOTH.value]))
    elif filters.purpose == ListingPurpose.SALE:
        conds.append(PropertyRow.purpose.in_([ListingPurpose.SALE.value, ListingPurpose.BOTH.value]))
    elif filters.purpose == ListingPurpose.BOTH:
        conds.append(PropertyRow.purpose == ListingPurpose.BOTH.value)

    if filters.property_types:
        conds.append(PropertyRow.property_type.in_([pt.value for pt in filters.property_types]))
    if filters.neighborhoods:
        slugs = [slugify(nb) for nb in filters.neighborhoods if slugify(nb)]
        if slugs:
            conds.append(PropertyRow.neighborhood_slug.in_(slugs))
    if filters.agency_slugs:
        conds.append(PropertyRow.agency_slug.in_(list(filters.agency_slugs)))

    if filters.only_similarity_candidates:
        conds.append(
            or_(
                PropertyRow.id.in_(
                    select(PropertyMatchRow.left_id).where(PropertyMatchRow.level == "review")
                ),
                PropertyRow.id.in_(
                    select(PropertyMatchRow.right_id).where(PropertyMatchRow.level == "review")
                ),
            )
        )

    if filters.only_with_price:
        conds.append(price_expr.is_not(None))
    if filters.min_price is not None:
        conds.append(price_expr >= filters.min_price)
    if filters.max_price is not None:
        conds.append(price_expr <= filters.max_price)
    if filters.max_monthly_total is not None:
        conds.append(PropertyRow.effective_monthly_total.is_not(None))
        conds.append(PropertyRow.effective_monthly_total <= filters.max_monthly_total)
    if filters.max_price_per_m2 is not None:
        conds.append(PropertyRow.price_per_m2.is_not(None))
        conds.append(PropertyRow.price_per_m2 <= filters.max_price_per_m2)

    if filters.min_area is not None:
        conds.append(PropertyRow.usable_area_m2 >= filters.min_area)
    if filters.max_area is not None:
        conds.append(PropertyRow.usable_area_m2 <= filters.max_area)

    if filters.min_bedrooms is not None:
        conds.append(PropertyRow.bedrooms >= filters.min_bedrooms)
    if filters.min_suites is not None:
        conds.append(PropertyRow.suites >= filters.min_suites)
    if filters.min_bathrooms is not None:
        conds.append(PropertyRow.bathrooms >= filters.min_bathrooms)
    if filters.min_parking is not None:
        conds.append(PropertyRow.parking_spots >= filters.min_parking)

    if filters.furnished == FurnishedStatus.FURNISHED:
        conds.append(PropertyRow.furnished_status == FurnishedStatus.FURNISHED.value)
    elif filters.furnished == FurnishedStatus.SEMI_FURNISHED:
        conds.append(
            PropertyRow.furnished_status.in_(
                [FurnishedStatus.FURNISHED.value, FurnishedStatus.SEMI_FURNISHED.value]
            )
        )
    elif filters.furnished == FurnishedStatus.UNFURNISHED:
        conds.append(PropertyRow.furnished_status == FurnishedStatus.UNFURNISHED.value)

    if filters.accepts_pets is True:
        conds.append(PropertyRow.accepts_pets.is_(True))
    if filters.has_barbecue is True:
        conds.append(PropertyRow.has_barbecue.is_(True))
    if filters.has_balcony is True:
        conds.append(PropertyRow.has_balcony.is_(True))
    if filters.has_elevator is True:
        conds.append(PropertyRow.has_elevator.is_(True))
    if filters.has_pool is True:
        conds.append(PropertyRow.has_pool.is_(True))
    if filters.has_yard is True:
        conds.append(PropertyRow.has_yard.is_(True))
    if filters.accepts_financing is True:
        conds.append(PropertyRow.accepts_financing.is_(True))

    if filters.only_price_drops:
        conds.append(and_(PropertyRow.price_delta_pct.is_not(None), PropertyRow.price_delta_pct < 0))

    if filters.q and filters.q.strip():
        q_clean = filters.q.strip()
        fts_expr = _build_fts_match_expression(q_clean)
        exact_code_cond = or_(
            PropertyRow.external_id == q_clean,
            func.lower(PropertyRow.ref_code) == q_clean.lower(),
        )
        if fts_expr:
            conds.append(or_(_fulltext_match(fts_expr), exact_code_cond))
        else:
            conds.append(exact_code_cond)

    return conds


def _build_sort_clauses(sort: SortOrder, price_expr: Any) -> tuple[Any, ...]:
    if sort == SortOrder.PRICE_ASC:
        return (case((price_expr.is_(None), 1), else_=0).asc(), price_expr.asc(), PropertyRow.id.desc())
    if sort == SortOrder.PRICE_DESC:
        return (case((price_expr.is_(None), 1), else_=0).asc(), price_expr.desc(), PropertyRow.id.desc())
    if sort == SortOrder.MONTHLY_TOTAL_ASC:
        col = func.coalesce(PropertyRow.effective_monthly_total, price_expr)
        return (case((col.is_(None), 1), else_=0).asc(), col.asc(), PropertyRow.id.desc())
    if sort == SortOrder.PRICE_PER_M2_ASC:
        return (
            case((PropertyRow.price_per_m2.is_(None), 1), else_=0).asc(),
            PropertyRow.price_per_m2.asc(),
            PropertyRow.id.desc(),
        )
    if sort == SortOrder.AREA_DESC:
        return (
            case((PropertyRow.usable_area_m2.is_(None), 1), else_=0).asc(),
            PropertyRow.usable_area_m2.desc(),
            PropertyRow.id.desc(),
        )
    if sort == SortOrder.PRICE_DROP:
        return (
            case(
                (and_(PropertyRow.price_delta_pct.is_not(None), PropertyRow.price_delta_pct < 0), 0),
                else_=1,
            ).asc(),
            PropertyRow.price_delta_pct.asc(),
            PropertyRow.id.desc(),
        )
    return (PropertyRow.first_seen_at.desc(), PropertyRow.id.desc())


def _decode_image_list(raw_json: str) -> list[str]:
    try:
        parsed = json.loads(raw_json)
        if isinstance(parsed, list):
            return [str(x) for x in parsed if isinstance(x, str) and x.startswith("http")]
    except ValueError:
        pass
    return []


def _to_property_summary(
    row: PropertyRow,
    agency_name: str,
    duplicate_count: int,
    min_group_price: float | None,
) -> PropertySummary:
    first_seen = _ensure_utc(row.first_seen_at) or _utc_now()
    last_seen = _ensure_utc(row.last_seen_at) or first_seen
    return PropertySummary(
        id=row.id,
        agency_slug=row.agency_slug,
        agency_name=agency_name,
        external_id=row.external_id,
        ref_code=row.ref_code,
        source_url=row.source_url,
        whatsapp_url=row.whatsapp_url,
        purpose=ListingPurpose(row.purpose),
        property_type=PropertyType(row.property_type),
        raw_type=row.raw_type,
        type_reconciled=row.type_reconciled,
        title=row.title,
        city=row.city,
        state=row.state,
        neighborhood=row.neighborhood,
        street=row.street,
        building_name=row.building_name,
        sale_price=row.sale_price,
        rent_price=row.rent_price,
        condo_fee=row.condo_fee,
        iptu_fee=row.iptu_fee,
        effective_monthly_total=row.effective_monthly_total,
        price_per_m2=row.price_per_m2,
        price_on_request=row.price_on_request,
        previous_price=row.previous_price,
        price_delta_pct=row.price_delta_pct,
        area_private_m2=row.area_private_m2,
        area_built_m2=row.area_built_m2,
        area_total_m2=row.area_total_m2,
        usable_area_m2=row.usable_area_m2,
        bedrooms=row.bedrooms,
        suites=row.suites,
        bathrooms=row.bathrooms,
        parking_spots=row.parking_spots,
        furnished_status=FurnishedStatus(row.furnished_status),
        accepts_pets=row.accepts_pets,
        has_elevator=row.has_elevator,
        has_pool=row.has_pool,
        has_barbecue=row.has_barbecue,
        has_balcony=row.has_balcony,
        has_air_conditioning=row.has_air_conditioning,
        has_solar=row.has_solar,
        has_yard=row.has_yard,
        accepts_financing=row.accepts_financing,
        accepts_exchange=row.accepts_exchange,
        accepts_proposals=row.accepts_proposals,
        latitude=row.latitude,
        longitude=row.longitude,
        cover_image_url=row.cover_image_url,
        image_count=row.image_count,
        dedup_group_key=row.dedup_group_key,
        duplicate_count=duplicate_count,
        min_group_price=min_group_price,
        is_active=row.is_active,
        detail_enriched_at=_ensure_utc(row.detail_enriched_at),
        first_seen_at=first_seen,
        last_seen_at=last_seen,
    )


def _similarity_listing(row: PropertyRow) -> SimilarityListing:
    return SimilarityListing(
        id=row.id,
        agency_slug=row.agency_slug,
        purpose=row.purpose,
        property_type=row.property_type,
        neighborhood=row.neighborhood_slug,
        street=row.street,
        street_number=row.street_number,
        building=row.building_name,
        title=row.title,
        description=row.description or "",
        area_private=row.area_private_m2,
        area_built=row.area_built_m2,
        area_total=row.area_total_m2,
        bedrooms=row.bedrooms,
        suites=row.suites,
        bathrooms=row.bathrooms,
        parking=row.parking_spots,
    )


def _ordered_pair(first_id: int, second_id: int) -> tuple[int, int]:
    """Return a pair as `(smaller, larger)`; the database no longer checks this order (MySQL 3823)."""
    if first_id == second_id:
        raise PersistenceError(f"Refusing to store a similarity pair of property {first_id} with itself")
    return (first_id, second_id) if first_id < second_id else (second_id, first_id)


def _rebuild_similarity_groups(session: Session) -> None:
    rows = session.scalars(select(PropertyRow).order_by(PropertyRow.id)).all()
    pairs = find_similarity_pairs(tuple(_similarity_listing(row) for row in rows if row.is_active))
    keys = group_similarity_pairs(pairs)
    session.execute(delete(PropertyMatchRow))
    session.add_all(
        [
            PropertyMatchRow(
                left_id=left_id,
                right_id=right_id,
                level=pair.assessment.level,
                score=pair.assessment.score,
                reasons_json=json.dumps(pair.assessment.reasons, ensure_ascii=False),
                uncertainties_json=json.dumps(pair.assessment.uncertainties, ensure_ascii=False),
            )
            for pair in pairs
            for left_id, right_id in (_ordered_pair(pair.left_id, pair.right_id),)
        ]
    )
    for row in rows:
        key = keys.get(row.id)
        if row.dedup_group_key != key:
            row.dedup_group_key = key


def _build_detail_with_peers(session: Session, row: PropertyRow) -> PropertyDetail:
    peers: list[DuplicatePeer] = []
    min_group_price: float | None = row.rent_price or row.sale_price
    agency_set = {row.agency_slug}

    if row.dedup_group_key:
        peer_rows = session.execute(
            select(PropertyRow, AgencyRow.name)
            .join(AgencyRow, PropertyRow.agency_slug == AgencyRow.slug)
            .where(
                PropertyRow.dedup_group_key == row.dedup_group_key,
                PropertyRow.id != row.id,
                PropertyRow.is_active.is_(True),
            )
            .order_by(func.coalesce(PropertyRow.rent_price, PropertyRow.sale_price, 999999999).asc())
        ).all()
        for p_row, p_agency_name in peer_rows:
            assessment = assess_similarity(_similarity_listing(row), _similarity_listing(p_row))
            agency_set.add(p_row.agency_slug)
            p_price = p_row.rent_price or p_row.sale_price
            if p_price is not None and (min_group_price is None or p_price < min_group_price):
                min_group_price = p_price
            peers.append(
                DuplicatePeer(
                    id=p_row.id,
                    agency_slug=p_row.agency_slug,
                    agency_name=p_agency_name,
                    ref_code=p_row.ref_code,
                    sale_price=p_row.sale_price,
                    rent_price=p_row.rent_price,
                    effective_monthly_total=p_row.effective_monthly_total,
                    source_url=p_row.source_url,
                    match_reasons=assessment.reasons,
                    match_uncertainties=assessment.uncertainties,
                    match_score=assessment.score,
                )
            )

    candidate_condition = and_(
        PropertyMatchRow.level == "review",
        or_(PropertyMatchRow.left_id == row.id, PropertyMatchRow.right_id == row.id),
    )
    candidate_total = int(
        session.scalar(select(func.count()).select_from(PropertyMatchRow).where(candidate_condition)) or 0
    )
    candidate_id = case(
        (PropertyMatchRow.left_id == row.id, PropertyMatchRow.right_id), else_=PropertyMatchRow.left_id
    )
    candidate_rows = session.execute(
        select(PropertyMatchRow, PropertyRow, AgencyRow.name)
        .select_from(PropertyMatchRow)
        .join(PropertyRow, PropertyRow.id == candidate_id)
        .join(AgencyRow, PropertyRow.agency_slug == AgencyRow.slug)
        .where(candidate_condition)
        .order_by(PropertyMatchRow.score.desc(), PropertyRow.id)
        .limit(20)
    ).all()
    candidates = tuple(
        DuplicatePeer(
            id=other.id,
            agency_slug=other.agency_slug,
            agency_name=agency_name,
            ref_code=other.ref_code,
            sale_price=other.sale_price,
            rent_price=other.rent_price,
            effective_monthly_total=other.effective_monthly_total,
            source_url=other.source_url,
            match_reasons=tuple(json.loads(pair.reasons_json)),
            match_uncertainties=tuple(json.loads(pair.uncertainties_json)),
            match_score=pair.score,
        )
        for pair, other, agency_name in candidate_rows
    )

    summary = _to_property_summary(
        row,
        row.agency.name if row.agency else row.agency_slug,
        len(agency_set),
        min_group_price,
    )
    history = tuple(
        PriceHistoryEntry(
            recorded_at=_ensure_utc(h.recorded_at) or _utc_now(),
            sale_price=h.sale_price,
            rent_price=h.rent_price,
            condo_fee=h.condo_fee,
            iptu_fee=h.iptu_fee,
            effective_monthly_total=h.effective_monthly_total,
        )
        for h in row.price_history
    )
    return PropertyDetail(
        **summary.model_dump(),
        description=row.description,
        street_number=row.street_number,
        postal_code=row.postal_code,
        insurance_fee=row.insurance_fee,
        image_urls=tuple(_decode_image_list(row.image_urls_json)),
        price_history=history,
        duplicate_peers=tuple(peers),
        similarity_candidates=candidates,
        similarity_candidates_total=candidate_total,
    )

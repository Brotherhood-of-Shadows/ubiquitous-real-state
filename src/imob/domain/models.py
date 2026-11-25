"""Immutable Pydantic v2 domain models for listings, filters, and sync reports."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from imob.domain.enums import (
    FurnishedStatus,
    ListingPurpose,
    PlatformKind,
    PropertyType,
    SortOrder,
    SyncStatus,
)


class AgencyConfig(BaseModel):
    """Static definition of a catalog source and its integration platform."""

    model_config = ConfigDict(frozen=True)

    slug: str
    name: str
    base_url: str
    platform: PlatformKind
    entry_urls: tuple[tuple[ListingPurpose, str], ...] = ()
    catalog_form: tuple[tuple[str, str], ...] = ()
    default_city_assumed: bool = True


class AgencyStatusView(BaseModel):
    """Read model summarizing an agency and its synchronization state."""

    model_config = ConfigDict(frozen=True)

    slug: str
    name: str
    base_url: str
    platform: PlatformKind
    last_synced_at: datetime | None = None
    last_sync_status: SyncStatus | None = None
    last_error: str | None = None
    active_properties: int = 0
    total_properties: int = 0


class RawScrapedItem(BaseModel):
    """Untrusted raw property payload extracted from an external listing or JSON API."""

    model_config = ConfigDict(frozen=True)

    agency_slug: str
    external_id: str
    source_url: str
    is_available: bool = True
    raw_purpose: str = ""
    raw_type: str = ""
    ref_code: str | None = None
    title: str = ""
    description: str | None = None
    raw_city: str | None = None
    raw_state: str | None = None
    raw_neighborhood: str | None = None
    street: str | None = None
    street_number: str | None = None
    postal_code: str | None = None
    building_name: str | None = None
    sale_price: float | None = None
    rent_price: float | None = None
    previous_price: float | None = None
    condo_fee: float | None = None
    iptu_fee: float | None = None
    insurance_fee: float | None = None
    area_private_m2: float | None = None
    area_built_m2: float | None = None
    area_total_m2: float | None = None
    bedrooms: int | None = None
    suites: int | None = None
    bathrooms: int | None = None
    parking_spots: int | None = None
    furnished_hint: str | None = None
    accepts_pets: bool | None = None
    has_elevator: bool | None = None
    has_pool: bool | None = None
    has_barbecue: bool | None = None
    has_balcony: bool | None = None
    has_air_conditioning: bool | None = None
    has_solar: bool | None = None
    has_yard: bool | None = None
    accepts_financing: bool | None = None
    accepts_exchange: bool | None = None
    accepts_proposals: bool | None = None
    latitude: float | None = None
    longitude: float | None = None
    whatsapp_url: str | None = None
    image_urls: tuple[str, ...] = ()
    detail_complete: bool = False


class NormalizedListing(BaseModel):
    """Validated and enriched property listing ready for database persistence."""

    model_config = ConfigDict(frozen=True)

    agency_slug: str
    external_id: str
    ref_code: str | None
    source_url: str
    whatsapp_url: str | None
    purpose: ListingPurpose
    property_type: PropertyType
    raw_type: str
    raw_purpose: str
    type_reconciled: bool
    title: str
    description: str | None
    city: str
    state: str
    neighborhood: str | None
    neighborhood_slug: str | None
    street: str | None
    street_number: str | None
    postal_code: str | None
    building_name: str | None
    sale_price: float | None
    rent_price: float | None
    condo_fee: float | None
    iptu_fee: float | None
    insurance_fee: float | None
    effective_monthly_total: float | None
    price_per_m2: float | None
    price_on_request: bool
    previous_listed_price: float | None
    area_private_m2: float | None
    area_built_m2: float | None
    area_total_m2: float | None
    usable_area_m2: float | None
    bedrooms: int | None
    suites: int | None
    bathrooms: int | None
    parking_spots: int | None
    furnished_status: FurnishedStatus
    accepts_pets: bool
    has_elevator: bool
    has_pool: bool
    has_barbecue: bool
    has_balcony: bool
    has_air_conditioning: bool
    has_solar: bool
    has_yard: bool
    accepts_financing: bool
    accepts_exchange: bool
    accepts_proposals: bool = False
    latitude: float | None
    longitude: float | None
    cover_image_url: str | None
    image_urls: tuple[str, ...]
    dedup_group_key: str | None
    fingerprint_hash: str
    detail_complete: bool


class PropertyDetailPatch(BaseModel):
    """Supplemental details fetched lazily from an individual property page."""

    model_config = ConfigDict(frozen=True)

    description: str | None = None
    condo_fee: float | None = None
    iptu_fee: float | None = None
    area_private_m2: float | None = None
    area_built_m2: float | None = None
    area_total_m2: float | None = None
    bedrooms: int | None = None
    suites: int | None = None
    bathrooms: int | None = None
    parking_spots: int | None = None
    neighborhood: str | None = None
    street: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    furnished_status: FurnishedStatus | None = None
    accepts_pets: bool | None = None
    has_elevator: bool | None = None
    has_pool: bool | None = None
    has_barbecue: bool | None = None
    has_balcony: bool | None = None
    has_air_conditioning: bool | None = None
    has_solar: bool | None = None
    has_yard: bool | None = None
    accepts_financing: bool | None = None
    accepts_exchange: bool | None = None
    accepts_proposals: bool | None = None
    image_urls: tuple[str, ...] = ()


class PriceHistoryEntry(BaseModel):
    """Historical snapshot recorded whenever a property's prices change."""

    model_config = ConfigDict(frozen=True)

    recorded_at: datetime
    sale_price: float | None
    rent_price: float | None
    condo_fee: float | None
    iptu_fee: float | None
    effective_monthly_total: float | None


class DuplicatePeer(BaseModel):
    """Another listing likely representing the same physical property."""

    model_config = ConfigDict(frozen=True)

    id: int
    agency_slug: str
    agency_name: str
    ref_code: str | None
    sale_price: float | None
    rent_price: float | None
    effective_monthly_total: float | None
    source_url: str
    match_reasons: tuple[str, ...] = ()
    match_uncertainties: tuple[str, ...] = ()
    match_score: int = 0


class PropertySummary(BaseModel):
    """Read model returned in paginated search results."""

    model_config = ConfigDict(frozen=True)

    id: int
    agency_slug: str
    agency_name: str
    external_id: str
    ref_code: str | None
    source_url: str
    whatsapp_url: str | None
    purpose: ListingPurpose
    property_type: PropertyType
    raw_type: str
    type_reconciled: bool
    title: str
    city: str
    state: str
    neighborhood: str | None
    street: str | None
    building_name: str | None
    sale_price: float | None
    rent_price: float | None
    condo_fee: float | None
    iptu_fee: float | None
    effective_monthly_total: float | None
    price_per_m2: float | None
    price_on_request: bool
    previous_price: float | None
    price_delta_pct: float | None
    area_private_m2: float | None
    area_built_m2: float | None
    area_total_m2: float | None
    usable_area_m2: float | None
    bedrooms: int | None
    suites: int | None
    bathrooms: int | None
    parking_spots: int | None
    furnished_status: FurnishedStatus
    accepts_pets: bool
    has_elevator: bool
    has_pool: bool
    has_barbecue: bool
    has_balcony: bool
    has_air_conditioning: bool
    has_solar: bool
    has_yard: bool
    accepts_financing: bool
    accepts_exchange: bool
    accepts_proposals: bool = False
    latitude: float | None
    longitude: float | None
    cover_image_url: str | None
    image_count: int
    dedup_group_key: str | None
    duplicate_count: int = 1
    min_group_price: float | None = None
    is_active: bool
    detail_enriched_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime


class PropertyDetail(PropertySummary):
    """Full property read model including description, gallery, history, and peers."""

    description: str | None = None
    street_number: str | None = None
    postal_code: str | None = None
    insurance_fee: float | None = None
    image_urls: tuple[str, ...] = ()
    price_history: tuple[PriceHistoryEntry, ...] = ()
    duplicate_peers: tuple[DuplicatePeer, ...] = ()
    similarity_candidates: tuple[DuplicatePeer, ...] = ()
    similarity_candidates_total: int = 0


MAX_INCLUDE_KEYS = 200
MAX_EXCLUDE_KEYS = 500
MAX_QUERY_LENGTH = 120


class ListingKey(BaseModel):
    """Stable listing identity that survives database rebuilds: agency slug plus agency listing ID.

    Personal browser state refers to listings by this key, never by the transient database ID,
    so a reused ID after a reset can never select or hide a different listing.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    agency_slug: str = Field(min_length=1, max_length=48, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    external_id: str = Field(min_length=1, max_length=96)


class PropertyFilterParams(BaseModel):
    """Validated query parameters for searching and filtering listings."""

    model_config = ConfigDict(frozen=True)

    q: str | None = Field(default=None, max_length=MAX_QUERY_LENGTH)
    purpose: ListingPurpose | None = None
    property_types: tuple[PropertyType, ...] = ()
    neighborhoods: tuple[str, ...] = ()
    agency_slugs: tuple[str, ...] = ()
    min_price: float | None = Field(default=None, ge=0)
    max_price: float | None = Field(default=None, ge=0)
    max_monthly_total: float | None = Field(default=None, ge=0)
    max_price_per_m2: float | None = Field(default=None, ge=0)
    min_area: float | None = Field(default=None, ge=0)
    max_area: float | None = Field(default=None, ge=0)
    min_bedrooms: int | None = Field(default=None, ge=0)
    min_suites: int | None = Field(default=None, ge=0)
    min_bathrooms: int | None = Field(default=None, ge=0)
    min_parking: int | None = Field(default=None, ge=0)
    furnished: FurnishedStatus | None = None
    accepts_pets: bool | None = None
    has_barbecue: bool | None = None
    has_balcony: bool | None = None
    has_elevator: bool | None = None
    has_pool: bool | None = None
    has_yard: bool | None = None
    accepts_financing: bool | None = None
    only_with_price: bool = False
    only_price_drops: bool = False
    # `None` disables the allow-list; an empty tuple matches nothing ("only favorites", none saved).
    include_keys: tuple[ListingKey, ...] | None = Field(default=None, max_length=MAX_INCLUDE_KEYS)
    exclude_keys: tuple[ListingKey, ...] = Field(default=(), max_length=MAX_EXCLUDE_KEYS)
    include_inactive: bool = False
    only_similarity_candidates: bool = False
    deduplicate: bool = False
    sort: SortOrder = SortOrder.PRICE_ASC
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=30, ge=1, le=120)


class PaginatedProperties(BaseModel):
    """Paginated search result envelope."""

    model_config = ConfigDict(frozen=True)

    total: int
    page: int
    page_size: int
    total_pages: int
    items: tuple[PropertySummary, ...]


class FacetBucket(BaseModel):
    """Count bucket for dynamic UI filter dropdowns."""

    model_config = ConfigDict(frozen=True)

    value: str
    label: str
    count: int


class SearchFacets(BaseModel):
    """Aggregated filter facets computed over active properties."""

    model_config = ConfigDict(frozen=True)

    property_types: tuple[FacetBucket, ...]
    neighborhoods: tuple[FacetBucket, ...]
    agencies: tuple[FacetBucket, ...]
    purposes: tuple[FacetBucket, ...]


class CatalogStats(BaseModel):
    """High-level metrics about the local Pato Branco real-estate database."""

    model_config = ConfigDict(frozen=True)

    active_properties: int
    inactive_properties: int
    rent_count: int
    sale_count: int
    both_count: int
    agencies_total: int
    agencies_synced: int
    price_drops_count: int
    duplicate_groups_count: int
    similarity_review_pairs_count: int = 0
    last_global_sync_at: datetime | None


class AgencySyncReport(BaseModel):
    """Outcome summary of synchronizing a single real-estate agency."""

    model_config = ConfigDict(frozen=True)

    agency_slug: str
    agency_name: str
    status: SyncStatus
    requests_made: int = 0
    scraped_raw: int = 0
    accepted_city: int = 0
    inserted: int = 0
    updated: int = 0
    price_changes: int = 0
    deactivated: int = 0
    duration_seconds: float = 0.0
    error: str | None = None


class SyncBatchReport(BaseModel):
    """Outcome summary of a multi-agency synchronization run."""

    model_config = ConfigDict(frozen=True)

    started_at: datetime
    finished_at: datetime
    forced: bool
    agencies: tuple[AgencySyncReport, ...]
    total_requests: int
    total_accepted: int
    total_inserted: int
    total_updated: int
    total_price_changes: int
    total_deactivated: int

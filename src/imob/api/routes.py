"""Read-only REST API route handlers for property search, stats, and sync status."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from imob.api.schemas import PropertySearchRequest
from imob.domain.enums import FurnishedStatus, ListingPurpose, PropertyType, SortOrder
from imob.domain.models import (
    MAX_QUERY_LENGTH,
    AgencyStatusView,
    CatalogStats,
    PaginatedProperties,
    PropertyDetail,
    PropertyFilterParams,
    SearchFacets,
)
from imob.errors import DomainValidationError
from imob.services.catalog_service import CatalogService
from imob.services.sync_service import SyncRuntimeStatus, SyncService

router = APIRouter(prefix="/api")
_LIST_FIELDS = {"types", "neighborhoods", "agencies", "include_keys", "exclude_keys"}


def get_catalog_service(request: Request) -> CatalogService:
    """Resolve `CatalogService` from FastAPI application state.

    Args:
        request: Incoming HTTP request.

    Returns:
        Application `CatalogService` instance.
    """
    svc: CatalogService = request.app.state.catalog_service
    return svc


def get_sync_service(request: Request) -> SyncService:
    """Resolve `SyncService` from FastAPI application state.

    Args:
        request: Incoming HTTP request.

    Returns:
        Application `SyncService` instance.
    """
    svc: SyncService = request.app.state.sync_service
    return svc


def _split_csv_list(values: list[str] | None) -> tuple[str, ...]:
    if not values:
        return ()
    out: list[str] = []
    for raw in values:
        for piece in raw.split(","):
            cleaned = piece.strip()
            if cleaned:
                out.append(cleaned)
    return tuple(dict.fromkeys(out))


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Return service liveness status.

    Returns:
        Simple status dictionary.
    """
    return {"status": "ok", "city": "Pato Branco - PR"}


@router.get("/stats", response_model=CatalogStats)
async def get_stats(
    catalog: Annotated[CatalogService, Depends(get_catalog_service)],
) -> CatalogStats:
    """Return global Pato Branco property counts, price-drop totals, and sync metrics.

    Args:
        catalog: Injected `CatalogService`.

    Returns:
        `CatalogStats` snapshot.
    """
    return await catalog.get_stats()


@router.get("/facets", response_model=SearchFacets)
async def get_facets(
    catalog: Annotated[CatalogService, Depends(get_catalog_service)],
    purpose: Annotated[str | None, Query(description="Optional purpose filter: aluguel or venda")] = None,
) -> SearchFacets:
    """Return dynamic filter facet counts across active listings.

    Args:
        catalog: Injected `CatalogService`.
        purpose: Optional purpose filter (`aluguel` or `venda`).

    Returns:
        `SearchFacets` buckets.
    """
    return await catalog.get_facets(purpose=purpose)


@router.get("/agencies", response_model=tuple[AgencyStatusView, ...])
async def list_agencies(
    catalog: Annotated[CatalogService, Depends(get_catalog_service)],
) -> tuple[AgencyStatusView, ...]:
    """List registered Pato Branco agencies with active counts and last sync status.

    Args:
        catalog: Injected `CatalogService`.

    Returns:
        Tuple of `AgencyStatusView`.
    """
    return await catalog.list_agencies()


@router.get("/properties", response_model=PaginatedProperties)
async def search_properties(
    catalog: Annotated[CatalogService, Depends(get_catalog_service)],
    q: Annotated[
        str | None,
        Query(max_length=MAX_QUERY_LENGTH, description="Busca textual FTS5 ou código do imóvel"),
    ] = None,
    purpose: Annotated[ListingPurpose | None, Query()] = None,
    types: Annotated[list[str] | None, Query(description="Tipos de imóvel (CSV ou repetido)")] = None,
    neighborhoods: Annotated[list[str] | None, Query(description="Bairros (CSV ou repetido)")] = None,
    agencies: Annotated[list[str] | None, Query(description="Slugs das imobiliárias")] = None,
    min_price: Annotated[float | None, Query(ge=0)] = None,
    max_price: Annotated[float | None, Query(ge=0)] = None,
    max_monthly_total: Annotated[float | None, Query(ge=0)] = None,
    max_price_per_m2: Annotated[float | None, Query(ge=0)] = None,
    min_area: Annotated[float | None, Query(ge=0)] = None,
    max_area: Annotated[float | None, Query(ge=0)] = None,
    min_bedrooms: Annotated[int | None, Query(ge=0)] = None,
    min_suites: Annotated[int | None, Query(ge=0)] = None,
    min_bathrooms: Annotated[int | None, Query(ge=0)] = None,
    min_parking: Annotated[int | None, Query(ge=0)] = None,
    furnished: Annotated[FurnishedStatus | None, Query()] = None,
    accepts_pets: Annotated[bool | None, Query()] = None,
    has_barbecue: Annotated[bool | None, Query()] = None,
    has_balcony: Annotated[bool | None, Query()] = None,
    has_elevator: Annotated[bool | None, Query()] = None,
    has_pool: Annotated[bool | None, Query()] = None,
    has_yard: Annotated[bool | None, Query()] = None,
    accepts_financing: Annotated[bool | None, Query()] = None,
    only_with_price: Annotated[bool, Query()] = False,
    only_price_drops: Annotated[bool, Query()] = False,
    include_inactive: Annotated[bool, Query()] = False,
    only_similarity_candidates: Annotated[
        bool, Query(description="Somente anúncios com pares para revisar")
    ] = False,
    deduplicate: Annotated[bool, Query()] = False,
    sort: Annotated[SortOrder, Query()] = SortOrder.PRICE_ASC,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=120)] = 30,
) -> PaginatedProperties:
    """Search, filter, deduplicate, and sort properties across configured agencies.

    Args:
        catalog: Injected `CatalogService`.
        q: Optional FTS5 full-text query or reference code.
        purpose: Transaction purpose (`aluguel`, `venda`, `venda_e_aluguel`).
        types: Property categories to include.
        neighborhoods: Neighborhood names or slugs to include.
        agencies: Agency slugs to include.
        min_price: Minimum listing price in BRL.
        max_price: Maximum listing price in BRL.
        max_monthly_total: Maximum total monthly rent + condo + IPTU in BRL.
        max_price_per_m2: Maximum price per square meter in BRL/m².
        min_area: Minimum usable area in m².
        max_area: Maximum usable area in m².
        min_bedrooms: Minimum bedrooms.
        min_suites: Minimum suites.
        min_bathrooms: Minimum bathrooms.
        min_parking: Minimum parking spots.
        furnished: Furnished filter (`mobiliado`, `semimobiliado`, `nao_mobiliado`).
        accepts_pets: Require pet-friendly listing.
        has_barbecue: Require barbecue / gourmet area.
        has_balcony: Require balcony / terrace.
        has_elevator: Require elevator.
        has_pool: Require pool.
        has_yard: Require yard / extra land.
        accepts_financing: Require financing eligibility.
        only_with_price: Exclude 'Consulte' listings without a disclosed price.
        only_price_drops: Only return properties whose price decreased.
        include_inactive: Include historical off-market properties.
        only_similarity_candidates: Return listings with similarity pairs needing review.
        deduplicate: Collapse cross-agency duplicate listings to the lowest-priced entry.
        sort: Result ordering strategy.
        page: 1-based page number.
        page_size: Page size (1..120).

    Returns:
        `PaginatedProperties` result page.
    """
    try:
        request = PropertySearchRequest(
            q=q,
            purpose=purpose,
            types=_split_csv_list(types),
            neighborhoods=_split_csv_list(neighborhoods),
            agencies=_split_csv_list(agencies),
            min_price=min_price,
            max_price=max_price,
            max_monthly_total=max_monthly_total,
            max_price_per_m2=max_price_per_m2,
            min_area=min_area,
            max_area=max_area,
            min_bedrooms=min_bedrooms,
            min_suites=min_suites,
            min_bathrooms=min_bathrooms,
            min_parking=min_parking,
            furnished=furnished,
            accepts_pets=accepts_pets,
            has_barbecue=has_barbecue,
            has_balcony=has_balcony,
            has_elevator=has_elevator,
            has_pool=has_pool,
            has_yard=has_yard,
            accepts_financing=accepts_financing,
            only_with_price=only_with_price,
            only_price_drops=only_price_drops,
            include_inactive=include_inactive,
            only_similarity_candidates=only_similarity_candidates,
            deduplicate=deduplicate,
            sort=sort,
            page=page,
            page_size=page_size,
        )
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc
    return await catalog.search(_to_filter_params(request))


@router.post("/properties/search", response_model=PaginatedProperties)
async def search_properties_with_keys(
    body: PropertySearchRequest,
    catalog: Annotated[CatalogService, Depends(get_catalog_service)],
) -> PaginatedProperties:
    """Run the same read-only search as `GET /api/properties` with bounded listing-key lists.

    The browser keeps favorites and discarded listings locally and sends their stable
    `agency_slug` + `external_id` keys here, so long lists never hit URL-length limits.
    Notes never leave the browser.

    Args:
        body: Search filters plus optional `include_keys` / `exclude_keys`.
        catalog: Injected `CatalogService`.

    Returns:
        `PaginatedProperties` result page.
    """
    return await catalog.search(_to_filter_params(body))


def _to_filter_params(request: PropertySearchRequest) -> PropertyFilterParams:
    parsed_types: list[PropertyType] = []
    for t_str in _split_csv_list(list(request.types)):
        try:
            parsed_types.append(PropertyType(t_str))
        except ValueError as exc:
            raise DomainValidationError(f"Invalid property type '{t_str}'.") from exc
    fields = request.model_dump(exclude=_LIST_FIELDS)
    return PropertyFilterParams(
        **fields,
        property_types=tuple(parsed_types),
        neighborhoods=_split_csv_list(list(request.neighborhoods)),
        agency_slugs=_split_csv_list(list(request.agencies)),
        include_keys=request.include_keys,
        exclude_keys=request.exclude_keys,
    )


@router.get("/properties/{property_id}", response_model=PropertyDetail)
async def get_property_detail(
    property_id: int,
    catalog: Annotated[CatalogService, Depends(get_catalog_service)],
) -> PropertyDetail:
    """Return full details, price history, and duplicate peers for one property.

    Args:
        property_id: Internal database ID.
        catalog: Injected `CatalogService`.

    Returns:
        `PropertyDetail` object.
    """
    return await catalog.get_detail(property_id)


@router.get("/sync/status", response_model=SyncRuntimeStatus)
async def get_sync_status(
    sync_service: Annotated[SyncService, Depends(get_sync_service)],
) -> SyncRuntimeStatus:
    """Return live progress of any active sync run, timer interval, and last report.

    Args:
        sync_service: Injected `SyncService`.

    Returns:
        `SyncRuntimeStatus` snapshot.
    """
    return sync_service.get_runtime_status()

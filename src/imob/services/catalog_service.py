"""Catalog read service."""

from __future__ import annotations

import asyncio

from imob.domain.models import (
    AgencyStatusView,
    CatalogStats,
    PaginatedProperties,
    PropertyDetail,
    PropertyFilterParams,
    SearchFacets,
)
from imob.errors import DomainValidationError
from imob.ports import PropertyRepositoryProtocol


class CatalogService:
    """Provides read-only filtered property search, facets, stats, and agency status."""

    def __init__(self, repository: PropertyRepositoryProtocol) -> None:
        """Initialize catalog service with persistence boundary.

        Args:
            repository: Repository implementing `PropertyRepositoryProtocol`.
        """
        self._repo = repository

    async def search(self, filters: PropertyFilterParams) -> PaginatedProperties:
        """Validate filter ranges and execute paginated property search.

        Args:
            filters: Search criteria.

        Returns:
            `PaginatedProperties` page.

        Raises:
            DomainValidationError: When `min_price > max_price` or `min_area > max_area`.
        """
        if (
            filters.min_price is not None
            and filters.max_price is not None
            and filters.min_price > filters.max_price
        ):
            raise DomainValidationError("min_price cannot be greater than max_price.")
        if (
            filters.min_area is not None
            and filters.max_area is not None
            and filters.min_area > filters.max_area
        ):
            raise DomainValidationError("min_area cannot be greater than max_area.")
        return await asyncio.to_thread(self._repo.search_properties, filters)

    async def get_detail(self, property_id: int) -> PropertyDetail:
        """Retrieve complete details, price history, and duplicate peers for `property_id`.

        Args:
            property_id: Internal property ID.

        Returns:
            `PropertyDetail` record.
        """
        return await asyncio.to_thread(self._repo.get_property_detail, property_id)

    async def get_facets(self, purpose: str | None = None) -> SearchFacets:
        """Return dynamic filter facet counts across active listings.

        Args:
            purpose: Optional purpose scope (`aluguel` or `venda`).

        Returns:
            `SearchFacets` buckets.
        """
        return await asyncio.to_thread(self._repo.get_facets, purpose)

    async def get_stats(self) -> CatalogStats:
        """Return high-level Pato Branco catalog metrics.

        Returns:
            `CatalogStats` summary.
        """
        return await asyncio.to_thread(self._repo.get_stats)

    async def list_agencies(self) -> tuple[AgencyStatusView, ...]:
        """List catalog sources with their sync status and property counts.

        Returns:
            Tuple of `AgencyStatusView`.
        """
        return await asyncio.to_thread(self._repo.list_agencies)

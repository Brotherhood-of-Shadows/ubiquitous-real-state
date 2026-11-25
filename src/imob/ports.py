"""Structural interfaces (`typing.Protocol`) decoupling services from infrastructure."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from imob.domain.enums import PlatformKind, SyncStatus
from imob.domain.models import (
    AgencyConfig,
    AgencyStatusView,
    CatalogStats,
    NormalizedListing,
    PaginatedProperties,
    PropertyDetail,
    PropertyDetailPatch,
    PropertyFilterParams,
    SearchFacets,
)


@dataclass(frozen=True)
class ScrapeBatchResult:
    """Items and request metrics returned by scraping one agency catalog."""

    items: tuple[RawScrapedItemAlias, ...]
    requests_made: int
    had_section_errors: bool = False
    error_summary: str | None = None


# Use direct import after dataclass definition for clean readability
from imob.domain.models import RawScrapedItem as RawScrapedItemAlias  # noqa: E402


@dataclass(frozen=True)
class UpsertBatchOutcome:
    """Database persistence counts after applying a synchronized agency catalog."""

    inserted: int
    updated: int
    price_changes: int
    deactivated: int


class AgencyScraperProtocol(Protocol):
    """Protocol implemented by each platform scraper adapter."""

    async def fetch_catalog(self, agency: AgencyConfig, *, max_pages: int) -> ScrapeBatchResult:
        """Fetch paginated listing summaries for an agency.

        Args:
            agency: Target agency configuration.
            max_pages: Safety cap on pages per listing section.

        Returns:
            `ScrapeBatchResult` containing raw items and request count.
        """
        ...

    async def fetch_property_detail(self, agency: AgencyConfig, source_url: str) -> PropertyDetailPatch:
        """Fetch supplemental detail fields from a single property page on demand.

        Args:
            agency: Target agency configuration.
            source_url: Property detail page URL.

        Returns:
            `PropertyDetailPatch` with enriched description, fees, and gallery.
        """
        ...


class ScraperRegistryProtocol(Protocol):
    """Protocol mapping `PlatformKind` values to their scraper adapter."""

    def get_scraper(self, platform: PlatformKind) -> AgencyScraperProtocol:
        """Return the adapter responsible for `platform`.

        Args:
            platform: Platform identifier.

        Returns:
            Matching `AgencyScraperProtocol` instance.
        """
        ...


class PropertyRepositoryProtocol(Protocol):
    """Persistence boundary for agencies, properties, price history, and sync logs."""

    def seed_agencies(self, agencies: tuple[AgencyConfig, ...]) -> None:
        """Ensure all registered agencies exist in the database.

        Args:
            agencies: Registered agency definitions.
        """
        ...

    def list_agencies(self) -> tuple[AgencyStatusView, ...]:
        """Return all agencies with their sync status and property counts.

        Returns:
            Tuple of `AgencyStatusView` records.
        """
        ...

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
        """Upsert an agency's normalized listings, record price changes, and mark vanished items inactive.

        Args:
            agency: Target agency configuration.
            listings: Validated listings belonging to the agency.
            allow_deactivation: Whether missing previously active items may be deactivated.
            sync_status: Resulting status of the agency sync run.
            requests_made: HTTP requests executed during the run.
            duration_seconds: Wall-clock duration of the run.
            error_message: Optional error diagnostic if partial/failed.

        Returns:
            `UpsertBatchOutcome` counts.
        """
        ...

    def record_failed_sync(
        self,
        agency: AgencyConfig,
        *,
        requests_made: int,
        duration_seconds: float,
        error_message: str,
    ) -> None:
        """Persist a failed synchronization run without altering active properties.

        Args:
            agency: Target agency configuration.
            requests_made: HTTP requests executed before failure.
            duration_seconds: Wall-clock duration.
            error_message: Failure summary.
        """
        ...

    def search_properties(self, filters: PropertyFilterParams) -> PaginatedProperties:
        """Execute a filtered, sorted, paginated property search.

        Args:
            filters: Validated search filter criteria.

        Returns:
            `PaginatedProperties` result page.
        """
        ...

    def get_property_detail(self, property_id: int) -> PropertyDetail:
        """Retrieve full details, price history, and duplicate peers for one property.

        Args:
            property_id: Internal database ID.

        Returns:
            `PropertyDetail` domain object.

        Raises:
            EntityNotFoundError: When `property_id` does not exist.
        """
        ...

    def apply_detail_patch(self, property_id: int, patch: PropertyDetailPatch) -> PropertyDetail:
        """Persist lazily enriched fields onto an existing property record.

        Args:
            property_id: Internal database ID.
            patch: Supplemental fields scraped from the property page.

        Returns:
            Updated `PropertyDetail`.
        """
        ...

    def get_facets(self, purpose: str | None = None) -> SearchFacets:
        """Compute filter facet counts across active properties.

        Args:
            purpose: Optional purpose filter (`aluguel` or `venda`) to scope counts.

        Returns:
            `SearchFacets` buckets.
        """
        ...

    def get_stats(self) -> CatalogStats:
        """Return aggregate statistics for the catalog dashboard.

        Returns:
            `CatalogStats` summary.
        """
        ...

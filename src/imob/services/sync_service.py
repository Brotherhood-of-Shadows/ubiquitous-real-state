"""Incremental synchronization and background timer service."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict

from imob.config import AppSettings
from imob.domain.enums import SyncStatus
from imob.domain.models import (
    AgencyConfig,
    AgencyStatusView,
    AgencySyncReport,
    SyncBatchReport,
)
from imob.domain.normalizer import normalize_scraped_item
from imob.domain.registry import AGENCIES, get_agency_config
from imob.errors import DomainValidationError, ScraperError
from imob.ports import PropertyRepositoryProtocol, ScraperRegistryProtocol

logger = logging.getLogger(__name__)


class SyncRuntimeStatus(BaseModel):
    """Snapshot of the background synchronization state exposed via `/api/sync/status`."""

    model_config = ConfigDict(frozen=True)

    is_running: bool
    completed_agencies: int
    total_agencies: int
    active_agencies: tuple[str, ...]
    auto_sync_enabled: bool
    sync_interval_hours: float
    next_timer_check_at: datetime | None
    last_report: SyncBatchReport | None


class SyncService:
    """Orchestrates polite catalog synchronization, TTL caching, and on-demand enrichment."""

    def __init__(
        self,
        repository: PropertyRepositoryProtocol,
        scrapers: ScraperRegistryProtocol,
        settings: AppSettings,
    ) -> None:
        """Initialize the synchronization service.

        Args:
            repository: Persistence boundary implementing `PropertyRepositoryProtocol`.
            scrapers: Platform scraper registry implementing `ScraperRegistryProtocol`.
            settings: Immutable operational configuration.
        """
        self._repo = repository
        self._scrapers = scrapers
        self._settings = settings
        self._run_lock = asyncio.Lock()
        # SQLite permits only one writer; scraping remains concurrent, but commits do not.
        self._write_lock = asyncio.Lock()
        self._is_running = False
        self._completed = 0
        self._total = 0
        self._active_slugs: set[str] = set()
        self._last_report: SyncBatchReport | None = None
        self._next_check_at: datetime | None = None
        self._timer_task: asyncio.Task[None] | None = None

    def get_runtime_status(self) -> SyncRuntimeStatus:
        """Return an immutable snapshot of live synchronization state.

        Returns:
            `SyncRuntimeStatus` instance.
        """
        return SyncRuntimeStatus(
            is_running=self._is_running,
            completed_agencies=self._completed,
            total_agencies=self._total,
            active_agencies=tuple(sorted(self._active_slugs)),
            auto_sync_enabled=self._settings.auto_sync_enabled,
            sync_interval_hours=self._settings.sync_interval_hours,
            next_timer_check_at=self._next_check_at,
            last_report=self._last_report,
        )

    def start_periodic_timer(self) -> None:
        """Start the non-blocking background periodic TTL timer if enabled."""
        if not self._settings.auto_sync_enabled or self._timer_task is not None:
            return
        self._timer_task = asyncio.create_task(self._periodic_loop(), name="imob-sync-timer")

    async def stop_periodic_timer(self) -> None:
        """Cancel and await cleanup of the background periodic timer."""
        if self._timer_task is None:
            return
        self._timer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._timer_task
        self._timer_task = None
        self._next_check_at = None

    async def _periodic_loop(self) -> None:
        check_seconds = self._settings.timer_check_minutes * 60.0
        # Wait briefly after server boot so startup is 100% instant and non-intrusive
        self._next_check_at = datetime.now(UTC) + timedelta(seconds=check_seconds)
        while True:
            await asyncio.sleep(check_seconds)
            self._next_check_at = datetime.now(UTC) + timedelta(seconds=check_seconds)
            if self._run_lock.locked():
                continue
            try:
                await self.sync_agencies(force=False)
            except Exception:
                logger.exception("Unhandled error in periodic sync timer")

    async def sync_agencies(
        self,
        *,
        agency_slugs: tuple[str, ...] = (),
        force: bool = False,
    ) -> SyncBatchReport:
        """Synchronize all (or selected) agencies respecting per-agency TTL unless `force=True`.

        Args:
            agency_slugs: Optional subset of agency slugs to synchronize.
            force: Bypass `sync_interval_hours` freshness check when `True`.

        Returns:
            `SyncBatchReport` summarizing all agency outcomes.

        Raises:
            DomainValidationError: When an unknown agency slug is requested or a sync is already running.
        """
        if agency_slugs:
            targets: list[AgencyConfig] = []
            for slug in agency_slugs:
                cfg = get_agency_config(slug)
                if cfg is None:
                    raise DomainValidationError(f"Unknown agency slug '{slug}'.")
                targets.append(cfg)
        else:
            targets = list(AGENCIES)

        if self._run_lock.locked():
            raise DomainValidationError("A synchronization run is already in progress.")

        async with self._run_lock:
            started_at = datetime.now(UTC)
            self._is_running = True
            self._completed = 0
            self._total = len(targets)
            self._active_slugs.clear()

            try:
                await asyncio.to_thread(self._repo.seed_agencies, AGENCIES)
                status_views = await asyncio.to_thread(self._repo.list_agencies)
                views_by_slug = {v.slug: v for v in status_views}

                sem = asyncio.Semaphore(self._settings.max_concurrent_agencies)

                async def _worker(cfg: AgencyConfig) -> AgencySyncReport:
                    async with sem:
                        self._active_slugs.add(cfg.slug)
                        try:
                            return await self._sync_single_agency(
                                cfg,
                                existing_view=views_by_slug.get(cfg.slug),
                                force=force,
                            )
                        finally:
                            self._active_slugs.discard(cfg.slug)
                            self._completed += 1

                reports = await asyncio.gather(*(_worker(cfg) for cfg in targets))
                finished_at = datetime.now(UTC)
                batch_report = SyncBatchReport(
                    started_at=started_at,
                    finished_at=finished_at,
                    forced=force,
                    agencies=tuple(reports),
                    total_requests=sum(r.requests_made for r in reports),
                    total_accepted=sum(r.accepted_city for r in reports),
                    total_inserted=sum(r.inserted for r in reports),
                    total_updated=sum(r.updated for r in reports),
                    total_price_changes=sum(r.price_changes for r in reports),
                    total_deactivated=sum(r.deactivated for r in reports),
                )
                self._last_report = batch_report
                return batch_report
            finally:
                self._is_running = False
                self._active_slugs.clear()

    async def _sync_single_agency(
        self,
        agency: AgencyConfig,
        *,
        existing_view: AgencyStatusView | None,
        force: bool,
    ) -> AgencySyncReport:
        if not force and existing_view and existing_view.last_synced_at:
            age = datetime.now(UTC) - existing_view.last_synced_at
            if existing_view.last_sync_status == SyncStatus.SUCCESS and age < timedelta(
                hours=self._settings.sync_interval_hours
            ):
                return AgencySyncReport(
                    agency_slug=agency.slug,
                    agency_name=agency.name,
                    status=SyncStatus.SKIPPED_TTL,
                )

        t0 = time.monotonic()
        scraper = self._scrapers.get_scraper(agency.platform)
        try:
            batch = await scraper.fetch_catalog(agency, max_pages=self._settings.max_pages_per_section)
        except ScraperError as exc:
            duration = round(time.monotonic() - t0, 2)
            async with self._write_lock:
                await asyncio.to_thread(
                    self._repo.record_failed_sync,
                    agency,
                    requests_made=1,
                    duration_seconds=duration,
                    error_message=str(exc),
                )
            return AgencySyncReport(
                agency_slug=agency.slug,
                agency_name=agency.name,
                status=SyncStatus.FAILED,
                requests_made=1,
                duration_seconds=duration,
                error=str(exc),
            )

        duration = round(time.monotonic() - t0, 2)
        if not batch.items and batch.had_section_errors:
            err_msg = batch.error_summary or "All listing sections failed"
            async with self._write_lock:
                await asyncio.to_thread(
                    self._repo.record_failed_sync,
                    agency,
                    requests_made=batch.requests_made,
                    duration_seconds=duration,
                    error_message=err_msg,
                )
            return AgencySyncReport(
                agency_slug=agency.slug,
                agency_name=agency.name,
                status=SyncStatus.FAILED,
                requests_made=batch.requests_made,
                duration_seconds=duration,
                error=err_msg,
            )

        normalized = tuple(
            norm
            for raw in batch.items
            for norm in [
                normalize_scraped_item(
                    raw,
                    agency,
                    target_city=self._settings.target_city,
                    target_state=self._settings.target_state,
                )
            ]
            if norm is not None
        )

        run_status = SyncStatus.PARTIAL if batch.had_section_errors else SyncStatus.SUCCESS
        allow_deactivation = (not batch.had_section_errors) and len(normalized) > 0

        async with self._write_lock:
            outcome = await asyncio.to_thread(
                self._repo.apply_agency_catalog,
                agency,
                normalized,
                allow_deactivation=allow_deactivation,
                sync_status=run_status,
                requests_made=batch.requests_made,
                duration_seconds=duration,
                error_message=batch.error_summary,
            )

        return AgencySyncReport(
            agency_slug=agency.slug,
            agency_name=agency.name,
            status=run_status,
            requests_made=batch.requests_made,
            scraped_raw=len(batch.items),
            accepted_city=len(normalized),
            inserted=outcome.inserted,
            updated=outcome.updated,
            price_changes=outcome.price_changes,
            deactivated=outcome.deactivated,
            duration_seconds=duration,
            error=batch.error_summary,
        )

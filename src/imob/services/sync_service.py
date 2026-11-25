"""Incremental synchronization and background timer service."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from time import monotonic
from zoneinfo import ZoneInfo

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

MAX_SCHEDULER_SLEEP_SECONDS = 60.0
MAX_SLOT_LATENESS_SECONDS = 300.0


def _resolve_local_slot_utc(candidate_date: date, slot_time: time, tz_info: ZoneInfo) -> datetime:
    """Resolve `(candidate_date, slot_time)` in `tz_info` to UTC, handling DST folds and gaps."""
    naive_target = datetime.combine(candidate_date, slot_time)
    dt_fold0 = naive_target.replace(tzinfo=tz_info, fold=0)
    utc_fold0 = dt_fold0.astimezone(UTC)
    roundtrip0 = utc_fold0.astimezone(tz_info)
    if roundtrip0.replace(tzinfo=None) == naive_target:
        # Normal time or first occurrence (`fold=0`) of an ambiguous fall-back hour.
        return utc_fold0

    dt_fold1 = naive_target.replace(tzinfo=tz_info, fold=1)
    utc_fold1 = dt_fold1.astimezone(UTC)
    roundtrip1 = utc_fold1.astimezone(tz_info)
    if roundtrip1.replace(tzinfo=None) == naive_target:
        return utc_fold1

    # Nonexistent wall-clock time inside a spring-forward gap: resolve to the transition instant
    # when the local clock jumps past `naive_target` (between `utc_fold1` and `utc_fold0`).
    low = min(utc_fold0, utc_fold1)
    high = max(utc_fold0, utc_fold1)
    after_offset = high.astimezone(tz_info).utcoffset()
    while (high - low) > timedelta(seconds=1):
        mid = low + (high - low) / 2
        mid = mid.replace(microsecond=0)
        if mid <= low or mid >= high:
            break
        if mid.astimezone(tz_info).utcoffset() == after_offset:
            high = mid
        else:
            low = mid
    return high


def next_run_at(
    now_utc: datetime,
    times: Sequence[time],
    tz: ZoneInfo | str = "America/Sao_Paulo",
) -> datetime:
    """Compute the next scheduled UTC execution instant strictly after `now_utc`.

    Args:
        now_utc: Current timezone-aware timestamp.
        times: Non-empty sequence of daily local `HH:MM` wall-clock run times.
        tz: IANA timezone name or `ZoneInfo` instance (defaults to `America/Sao_Paulo`).

    Returns:
        Timezone-aware UTC `datetime` of the earliest scheduled slot strictly after `now_utc`.

    Raises:
        ValueError: If `now_utc` is naive or `times` is empty.
    """
    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("now_utc must be a timezone-aware datetime.")
    if not times:
        raise ValueError("times must contain at least one scheduled time.")

    tz_info = ZoneInfo(tz) if isinstance(tz, str) else tz
    current_utc = now_utc.astimezone(UTC)
    current_local = current_utc.astimezone(tz_info)
    ordered_times = sorted({time(t.hour, t.minute, t.second, t.microsecond) for t in times})

    start_date = current_local.date()
    for day_offset in range(0, 8):
        candidate_date = start_date + timedelta(days=day_offset)
        candidates = sorted(
            _resolve_local_slot_utc(candidate_date, slot_time, tz_info) for slot_time in ordered_times
        )
        for candidate_utc in candidates:
            if candidate_utc > current_utc:
                return candidate_utc

    raise RuntimeError("Unable to compute next scheduled run within 7 days.")


async def _to_thread_uninterruptible[**P, T](
    func: Callable[P, T],
    /,
    *args: P.args,
    **kwargs: P.kwargs,
) -> tuple[T, bool]:
    """Run `func` in a worker thread and wait for it to finish even if cancelled."""
    task = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except BaseException:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
    if task.cancelled():
        raise asyncio.CancelledError
    exc = task.exception()
    if exc is not None:
        if cancelled:
            raise asyncio.CancelledError from None
        raise exc
    return task.result(), cancelled


async def _run_in_thread[**P, T](
    func: Callable[P, T],
    /,
    *args: P.args,
    **kwargs: P.kwargs,
) -> T:
    """Run `func` in a worker thread, draining it before propagating cancellation."""
    result, cancelled = await _to_thread_uninterruptible(func, *args, **kwargs)
    if cancelled:
        raise asyncio.CancelledError
    return result


async def _run_workers(
    worker_tasks: list[asyncio.Task[AgencySyncReport]],
) -> list[AgencySyncReport]:
    """Wait for agency worker tasks and drain active writes before propagating failure or cancel."""
    if not worker_tasks:
        return []
    cancelled = False
    first_error: BaseException | None = None
    try:
        done, _ = await asyncio.wait(worker_tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in done:
            if task.cancelled():
                cancelled = True
                break
            exc = task.exception()
            if exc is not None:
                first_error = exc
                break
    except asyncio.CancelledError:
        cancelled = True
    finally:
        if cancelled or first_error is not None or any(not t.done() for t in worker_tasks):
            for task in worker_tasks:
                if not task.done():
                    task.cancel()
            drain_fut = asyncio.gather(*worker_tasks, return_exceptions=True)
            while not drain_fut.done():
                try:
                    await asyncio.shield(drain_fut)
                except asyncio.CancelledError:
                    cancelled = True
            for task in worker_tasks:
                if not task.cancelled():
                    _ = task.exception()
    if cancelled:
        raise asyncio.CancelledError
    if first_error is not None:
        raise first_error
    return [task.result() for task in worker_tasks]


class SyncRuntimeStatus(BaseModel):
    """Snapshot of the background synchronization state exposed via `/api/sync/status`."""

    model_config = ConfigDict(frozen=True)

    is_running: bool
    completed_agencies: int
    total_agencies: int
    active_agencies: tuple[str, ...]
    auto_sync_enabled: bool
    sync_schedule: tuple[str, ...]
    sync_timezone: str
    next_scheduled_run_at: datetime | None
    last_report: SyncBatchReport | None


class SyncService:
    """Orchestrates polite catalog synchronization, CLI TTL caching, and fixed-time scheduled runs."""

    def __init__(
        self,
        repository: PropertyRepositoryProtocol,
        scrapers: ScraperRegistryProtocol,
        settings: AppSettings,
        *,
        now_fn: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
        max_sleep_seconds: float = MAX_SCHEDULER_SLEEP_SECONDS,
        max_slot_lateness_seconds: float = MAX_SLOT_LATENESS_SECONDS,
    ) -> None:
        """Initialize the synchronization service.

        Args:
            repository: Persistence boundary implementing `PropertyRepositoryProtocol`.
            scrapers: Platform scraper registry implementing `ScraperRegistryProtocol`.
            settings: Immutable operational configuration.
            now_fn: Optional clock function returning current UTC `datetime` (for deterministic tests).
            sleep_fn: Optional async sleep function taking seconds (for deterministic tests).
            max_sleep_seconds: Upper bound on a single scheduler sleep step so clock jumps are caught.
            max_slot_lateness_seconds: Maximum lateness before a missed slot is skipped without catch-up.
        """
        self._repo = repository
        self._scrapers = scrapers
        self._settings = settings
        self._now_fn: Callable[[], datetime] = now_fn or (lambda: datetime.now(UTC))
        self._sleep_fn: Callable[[float], Awaitable[None]] = sleep_fn or asyncio.sleep
        self._max_sleep_seconds = max_sleep_seconds
        self._max_slot_lateness_seconds = max_slot_lateness_seconds
        self._run_lock = asyncio.Lock()
        # Scraping stays concurrent but catalog commits run one at a time: each commit rebuilds the
        # global `property_matches` table, which would deadlock between concurrent InnoDB writers.
        self._write_lock = asyncio.Lock()
        self._is_running = False
        self._completed = 0
        self._total = 0
        self._active_slugs: set[str] = set()
        self._last_report: SyncBatchReport | None = None
        self._next_scheduled_run_at: datetime | None = None
        self._last_slot_utc: datetime | None = None
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
            sync_schedule=tuple(slot.strftime("%H:%M") for slot in self._settings.sync_schedule),
            sync_timezone=self._settings.sync_timezone,
            next_scheduled_run_at=self._next_scheduled_run_at,
            last_report=self._last_report,
        )

    def start_periodic_timer(self) -> None:
        """Start the non-blocking background fixed-time scheduler if enabled."""
        if not self._settings.auto_sync_enabled or self._timer_task is not None:
            return
        now_utc = self._now_fn()
        self._last_slot_utc = now_utc
        self._next_scheduled_run_at = next_run_at(
            now_utc,
            self._settings.sync_schedule,
            self._settings.sync_timezone,
        )
        self._timer_task = asyncio.create_task(self._periodic_loop(), name="imob-sync-timer")

    async def stop_periodic_timer(self) -> None:
        """Cancel and await cleanup of the background periodic timer."""
        if self._timer_task is None:
            return
        self._timer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._timer_task
        self._timer_task = None
        self._next_scheduled_run_at = None
        self._last_slot_utc = None

    async def _periodic_loop(self) -> None:
        schedule = self._settings.sync_schedule
        tz_name = self._settings.sync_timezone
        now_utc = self._now_fn()
        if self._last_slot_utc is None or now_utc > self._last_slot_utc:
            self._last_slot_utc = now_utc
        target_utc = next_run_at(self._last_slot_utc, schedule, tz_name)
        self._next_scheduled_run_at = target_utc

        while True:
            now_utc = self._now_fn()
            if now_utc < target_utc:
                remaining = (target_utc - now_utc).total_seconds()
                await self._sleep_fn(min(max(remaining, 0.0), self._max_sleep_seconds))
                continue

            lateness = (now_utc - target_utc).total_seconds()
            if lateness > self._max_slot_lateness_seconds:
                logger.warning(
                    "Skipping missed scheduled sync slot %s (current UTC is %s, late by %.1fs)",
                    target_utc.isoformat(),
                    now_utc.isoformat(),
                    lateness,
                )
                self._last_slot_utc = max(now_utc, target_utc)
                target_utc = next_run_at(self._last_slot_utc, schedule, tz_name)
                self._next_scheduled_run_at = target_utc
                continue

            self._last_slot_utc = max(now_utc, target_utc)
            try:
                await self.sync_agencies(force=True)
            except Exception:
                logger.exception("Unhandled error in scheduled sync run")

            after_utc = self._now_fn()
            if after_utc > self._last_slot_utc:
                self._last_slot_utc = after_utc
            target_utc = next_run_at(self._last_slot_utc, schedule, tz_name)
            self._next_scheduled_run_at = target_utc

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
            # Cross-process guard: a CLI `imob sync` and the server's scheduler never overlap.
            lease, acquire_cancelled = await _to_thread_uninterruptible(self._repo.try_acquire_sync_lock)
            if lease is None:
                if acquire_cancelled:
                    raise asyncio.CancelledError
                raise DomainValidationError("A synchronization run is already in progress.")
            try:
                if acquire_cancelled:
                    raise asyncio.CancelledError
                return await self._run_locked(targets, force=force)
            finally:
                _, release_cancelled = await _to_thread_uninterruptible(lease.release)
                if release_cancelled:
                    raise asyncio.CancelledError

    async def _run_locked(self, targets: list[AgencyConfig], *, force: bool) -> SyncBatchReport:
        started_at = datetime.now(UTC)
        self._is_running = True
        self._completed = 0
        self._total = len(targets)
        self._active_slugs.clear()

        try:
            await _run_in_thread(self._repo.seed_agencies, AGENCIES, sync_lock_held=True)
            status_views = await _run_in_thread(self._repo.list_agencies)
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

            worker_tasks = [asyncio.create_task(_worker(cfg)) for cfg in targets]
            reports = await _run_workers(worker_tasks)
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

        t0 = monotonic()
        scraper = self._scrapers.get_scraper(agency.platform)
        try:
            batch = await scraper.fetch_catalog(agency, max_pages=self._settings.max_pages_per_section)
        except ScraperError as exc:
            duration = round(monotonic() - t0, 2)
            async with self._write_lock:
                await _run_in_thread(
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

        duration = round(monotonic() - t0, 2)
        if not batch.items and batch.had_section_errors:
            err_msg = batch.error_summary or "All listing sections failed"
            async with self._write_lock:
                await _run_in_thread(
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
            outcome = await _run_in_thread(
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

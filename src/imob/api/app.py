"""FastAPI application factory, lifecycle management, error handlers, and static UI mount."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from imob.api.maps import router as maps_router
from imob.api.routes import router as api_router
from imob.config import AppSettings
from imob.db.engine import (
    create_session_factory,
    create_sqlite_engine,
    ensure_fts5_objects,
    run_migrations,
)
from imob.db.repository import SqlAlchemyPropertyRepository
from imob.domain.registry import AGENCIES
from imob.errors import (
    DomainValidationError,
    EntityNotFoundError,
    PersistenceError,
    ScraperError,
)
from imob.scrapers import PlatformScraperRegistry
from imob.scrapers.http_client import PoliteHttpClient
from imob.services.catalog_service import CatalogService
from imob.services.sync_service import SyncService

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_app(settings: AppSettings | None = None) -> FastAPI:
    """Construct the FastAPI application with explicit dependency lifetimes.

    Args:
        settings: Optional runtime settings override (useful for isolated tests).

    Returns:
        Configured `FastAPI` instance.
    """
    cfg = settings or AppSettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await asyncio.to_thread(run_migrations, cfg.db_path)
        engine = create_sqlite_engine(cfg.db_path)
        await asyncio.to_thread(ensure_fts5_objects, engine)
        session_factory = create_session_factory(engine)
        repo = SqlAlchemyPropertyRepository(session_factory)
        await asyncio.to_thread(repo.seed_agencies, AGENCIES)

        async with PoliteHttpClient(
            timeout_seconds=cfg.http_timeout_seconds,
            per_domain_delay_seconds=cfg.per_domain_delay_seconds,
        ) as http_client:
            scraper_registry = PlatformScraperRegistry(http_client, target_city=cfg.target_city)
            sync_service = SyncService(repo, scraper_registry, cfg)
            catalog_service = CatalogService(repo)

            app.state.settings = cfg
            app.state.catalog_service = catalog_service
            app.state.sync_service = sync_service

            sync_service.start_periodic_timer()
            try:
                yield
            finally:
                await sync_service.stop_periodic_timer()
                engine.dispose()

    app = FastAPI(
        title="Imob Pato Branco API",
        description=(
            "Agregador local, sincronizador incremental de baixo custo e filtro unificado "
            "para as imobiliárias cadastradas de Pato Branco - PR."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.exception_handler(EntityNotFoundError)
    async def _handle_not_found(_req: Request, exc: EntityNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"error": exc.code, "message": exc.message})

    @app.exception_handler(DomainValidationError)
    async def _handle_validation(_req: Request, exc: DomainValidationError) -> JSONResponse:
        status = 409 if "already in progress" in exc.message else 422
        return JSONResponse(status_code=status, content={"error": exc.code, "message": exc.message})

    @app.exception_handler(ScraperError)
    async def _handle_scraper(_req: Request, exc: ScraperError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"error": exc.code, "message": exc.message})

    @app.exception_handler(PersistenceError)
    async def _handle_persistence(_req: Request, exc: PersistenceError) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"error": exc.code, "message": "Database operation failed."},
        )

    app.include_router(api_router)
    app.include_router(maps_router)

    if _WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_WEB_DIR)), name="static")

        @app.get("/", include_in_schema=False)
        async def serve_dashboard() -> FileResponse:
            return FileResponse(str(_WEB_DIR / "index.html"))

    return app

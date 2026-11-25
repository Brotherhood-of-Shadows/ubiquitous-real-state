"""Read-only cartographic routes for public links and operator-imported quadra PDFs."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import FileResponse

from imob.config import AppSettings
from imob.maps import MapCatalog, get_map_catalog, get_quadra_pdf

router = APIRouter(prefix="/api/maps", tags=["Mapas"])
QuadraNumber = Annotated[int, Path(ge=1, le=2500)]


@router.get("", response_model=MapCatalog)
async def map_catalog(
    request: Request, quadra: Annotated[int | None, Query(ge=1, le=2500)] = None
) -> MapCatalog:
    """List public map sources and local PDF availability."""
    settings: AppSettings = request.app.state.settings
    return await asyncio.to_thread(get_map_catalog, settings.db_path.parent, quadra)


@router.get("/quadras/{quadra}/pdf", response_class=FileResponse)
async def quadra_pdf(quadra: QuadraNumber, request: Request) -> FileResponse:
    """Open one imported quadra PDF inline in the browser."""
    settings: AppSettings = request.app.state.settings
    path = await asyncio.to_thread(get_quadra_pdf, settings.db_path.parent, quadra)
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="quadra-{quadra:04d}.pdf"',
            "X-Content-Type-Options": "nosniff",
        },
    )

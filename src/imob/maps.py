"""Public cartographic resources and local quadra PDFs, without personal cadastral data."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from imob.errors import DomainValidationError, EntityNotFoundError, PersistenceError

MAX_PDF_BYTES = 20 * 1024 * 1024
MAP_SOURCE_URL = "https://patobranco.pr.gov.br/mapas-e-arquivos-para-download/"


class MapResource(BaseModel):
    """A public map link published by the municipality."""

    model_config = ConfigDict(frozen=True)
    title: str
    url: str


class QuadraMapRange(MapResource):
    """Official Drive folder containing maps in a numerical quadra range."""

    start: int
    end: int


class QuadraMapView(BaseModel):
    """Available public folder and optional local PDF for one quadra."""

    model_config = ConfigDict(frozen=True)
    quadra: int
    label: str
    folder_url: str
    local_pdf: bool


class MapCatalog(BaseModel):
    """Available cartographic links and imported PDFs; no owner or tax records."""

    model_config = ConfigDict(frozen=True)
    source_url: str = MAP_SOURCE_URL
    ranges: tuple[QuadraMapRange, ...]
    resources: tuple[MapResource, ...]
    local_quadras: tuple[int, ...]
    selected: QuadraMapView | None = None


_RANGES = tuple(
    QuadraMapRange(
        title=f"Quadras {start:04d}–{end:04d}",
        start=start,
        end=end,
        url=f"https://drive.google.com/drive/folders/{folder}",
    )
    for start, end, folder in (
        (1, 500, "1eD_0fyV2HQ712gSeiW6mw1u69z0P04qQ"),
        (501, 1000, "1ugpMVNYiEJ9xR-f-QRALX8JZT68ch8_7"),
        (1001, 1500, "1Ed1T9bJSkSUXkYGtU2l6-PksdQceUd6h"),
        (1501, 2000, "1TSE8tydFnAa6M3fJm8zCSh8myN-hSV2O"),
        (2001, 2500, "1NA3xUHhdzWXWEyU8q9QnTxGnOQ6ylWj4"),
    )
)
_RESOURCES = (
    MapResource(title="Mapas municipais e mapas por bairro", url=MAP_SOURCE_URL),
    MapResource(
        title="Mapa do Centro — PDF 2025",
        url="https://patobranco.pr.gov.br/wp-content/uploads/2025/02/Abairramento-e-Regionalizacao-Pato-Branco_2025-Centro.pdf",
    ),
    MapResource(
        title="Arquivos Shapefile",
        url="https://drive.google.com/drive/folders/130ns7IfM4jSJLv1IYRsj4Ek_SjNsSbdm",
    ),
)


def _validate_quadra(quadra: int) -> None:
    if not 1 <= quadra <= 2500:
        raise DomainValidationError("A quadra deve estar entre 1 e 2500")


def _pdf_path(data_root: Path, quadra: int) -> Path:
    _validate_quadra(quadra)
    return data_root / "maps" / "quadras" / f"{quadra:04d}.pdf"


def get_map_catalog(data_root: Path, quadra: int | None = None) -> MapCatalog:
    """Return map links and local PDF availability, optionally selecting a quadra."""
    selected = None
    if quadra is not None:
        path = _pdf_path(data_root, quadra)
        group = next(group for group in _RANGES if group.start <= quadra <= group.end)
        selected = QuadraMapView(
            quadra=quadra, label=f"{quadra:04d}", folder_url=group.url, local_pdf=path.is_file()
        )
    try:
        folder = data_root / "maps" / "quadras"
        local = tuple(
            sorted(
                int(path.stem)
                for path in folder.glob("*.pdf")
                if path.stem.isdigit()
                and 1 <= int(path.stem) <= 2500
                and path.name == f"{int(path.stem):04d}.pdf"
                and path.is_file()
            )
        )
    except OSError as exc:
        raise PersistenceError("Não foi possível consultar os mapas locais") from exc
    return MapCatalog(ranges=_RANGES, resources=_RESOURCES, selected=selected, local_quadras=local)


def get_quadra_pdf(data_root: Path, quadra: int) -> Path:
    """Resolve one imported quadra PDF, raising not-found when unavailable."""
    path = _pdf_path(data_root, quadra)
    if not path.is_file():
        raise EntityNotFoundError("PDF da quadra", quadra)
    return path


def save_quadra_pdf(data_root: Path, quadra: int, content: bytes) -> QuadraMapView:
    """Store a user-selected PDF atomically with a bounded size and a fixed filename."""
    target = _pdf_path(data_root, quadra)
    if len(content) > MAX_PDF_BYTES or not content.startswith(b"%PDF-"):
        raise DomainValidationError("Selecione um PDF válido de até 20 MB")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".map-upload-", dir=target.parent) as staging:
            staged = Path(staging) / "map.pdf"
            staged.write_bytes(content)
            staged.replace(target)
    except OSError as exc:
        raise PersistenceError("Não foi possível salvar o PDF da quadra") from exc
    group = next(group for group in _RANGES if group.start <= quadra <= group.end)
    return QuadraMapView(quadra=quadra, label=f"{quadra:04d}", folder_url=group.url, local_pdf=True)


def import_quadra_pdfs(data_root: Path, source: Path) -> tuple[tuple[int, ...], tuple[str, ...]]:
    """Import maps named 0928.pdf or Quadra_0928.pdf; skip ambiguous filenames.

    Duplicate identifiers fail before writing. Returns imported quadras and skipped PDF names.
    """
    if not source.is_dir():
        raise DomainValidationError("Informe uma pasta existente contendo os PDFs das quadras")
    planned: dict[int, Path] = {}
    skipped: list[str] = []
    try:
        for path in sorted(source.rglob("*")):
            if not path.is_file() or path.suffix.lower() != ".pdf":
                continue
            match = re.fullmatch(r"(?:quadra[ _-]*)?0*(\d{1,4})", path.stem, re.I)
            if not match or not 1 <= int(match.group(1)) <= 2500:
                skipped.append(str(path.relative_to(source)))
                continue
            quadra = int(match.group(1))
            if quadra in planned:
                raise DomainValidationError(f"Mais de um PDF identificado para a quadra {quadra:04d}")
            planned[quadra] = path
        for quadra, path in planned.items():
            if path.stat().st_size > MAX_PDF_BYTES:
                raise DomainValidationError(f"PDF da quadra {quadra:04d} excede 20 MB")
            save_quadra_pdf(data_root, quadra, path.read_bytes())
    except OSError as exc:
        raise PersistenceError("Não foi possível importar os PDFs das quadras") from exc
    return tuple(sorted(planned)), tuple(skipped)

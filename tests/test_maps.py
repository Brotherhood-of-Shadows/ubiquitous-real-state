"""Public map range resolution and local PDF library behavior."""

from pathlib import Path

import pytest

from imob.api.app import create_app
from imob.errors import DomainValidationError, EntityNotFoundError
from imob.maps import get_map_catalog, get_quadra_pdf, import_quadra_pdfs, save_quadra_pdf

_PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"


@pytest.mark.parametrize(
    ("quadra", "start", "end"),
    [
        (1, 1, 500),
        (500, 1, 500),
        (501, 501, 1000),
        (928, 501, 1000),
        (1000, 501, 1000),
        (1001, 1001, 1500),
        (1501, 1501, 2000),
        (2001, 2001, 2500),
        (2500, 2001, 2500),
    ],
)
def test_quadra_resolves_official_folder(tmp_path: Path, quadra: int, start: int, end: int) -> None:
    """Respect range boundaries without implying that every PDF was already downloaded."""
    catalog = get_map_catalog(tmp_path, quadra)
    group = next(group for group in catalog.ranges if group.start == start and group.end == end)
    assert catalog.selected is not None
    assert catalog.selected.folder_url == group.url
    assert catalog.selected.label == f"{quadra:04d}"
    assert not catalog.selected.local_pdf
    assert catalog.local_quadras == ()


@pytest.mark.parametrize("quadra", [0, -1, 2501, 1000000])
def test_invalid_quadra_cannot_create_paths(tmp_path: Path, quadra: int) -> None:
    """Reject identifiers outside the published map catalog."""
    with pytest.raises(DomainValidationError):
        save_quadra_pdf(tmp_path, quadra, _PDF)
    assert not (tmp_path / "maps").exists()


def test_local_pdf_can_be_saved_read_and_replaced(tmp_path: Path) -> None:
    """Persist user-selected bytes under a canonical name and update availability."""
    save_quadra_pdf(tmp_path, 928, _PDF)
    path = get_quadra_pdf(tmp_path, 928)
    assert path == tmp_path / "maps" / "quadras" / "0928.pdf"
    assert path.read_bytes() == _PDF
    replacement = _PDF + b"\n% revised\n"
    save_quadra_pdf(tmp_path, 928, replacement)
    assert path.read_bytes() == replacement
    catalog = get_map_catalog(tmp_path, 928)
    assert catalog.selected is not None and catalog.selected.local_pdf
    assert catalog.local_quadras == (928,)
    with pytest.raises(EntityNotFoundError):
        get_quadra_pdf(tmp_path, 1)
    with pytest.raises(DomainValidationError):
        save_quadra_pdf(tmp_path, 928, b"<html>not a map</html>")
    assert path.read_bytes() == replacement


def test_batch_import_skips_ambiguous_names_and_rejects_duplicates(tmp_path: Path) -> None:
    """Only explicit quadra filenames are indexed; duplicates are never silently overwritten."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "Quadra_0928.PDF").write_bytes(_PDF)
    (source / "0001.pdf").write_bytes(_PDF)
    (source / "mapa-geral.pdf").write_bytes(_PDF)
    imported, skipped = import_quadra_pdfs(tmp_path / "data", source)
    assert imported == (1, 928)
    assert skipped == ("mapa-geral.pdf",)
    (source / "928.pdf").write_bytes(_PDF)
    with pytest.raises(DomainValidationError, match="Mais de um PDF"):
        import_quadra_pdfs(tmp_path / "other-data", source)
    assert not (tmp_path / "other-data").exists()


def test_map_routes_are_exposed_without_municipal_cadastro_requests() -> None:
    """Expose read-only PDF download and map catalog endpoints; uploads are CLI-only."""
    schema = create_app().openapi()
    assert "/api/maps" in schema["paths"]
    assert set(schema["paths"]["/api/maps/quadras/{quadra}/pdf"]) == {"get"}

"""Public map range resolution, local PDF library behavior, and public-serving hardening."""

import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from imob.api.app import create_app
from imob.config import AppSettings
from imob.errors import DomainValidationError, EntityNotFoundError
from imob.maps import get_map_catalog, get_quadra_pdf, import_quadra_pdfs, save_quadra_pdf

_PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"
_EXPECTED_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' https: data:; "
    "connect-src 'self'; "
    "frame-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'self'"
)
_WEB_DIR = Path(__file__).resolve().parent.parent / "src" / "imob" / "web"


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
    assert not (tmp_path / "quadras").exists()


def test_local_pdf_can_be_saved_read_and_replaced(tmp_path: Path) -> None:
    """Persist user-selected bytes under a canonical name and update availability."""
    save_quadra_pdf(tmp_path, 928, _PDF)
    path = get_quadra_pdf(tmp_path, 928)
    assert path == tmp_path / "quadras" / "0928.pdf"
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
    imported, skipped = import_quadra_pdfs(tmp_path / "maps", source)
    assert imported == (1, 928)
    assert skipped == ("mapa-geral.pdf",)
    (source / "928.pdf").write_bytes(_PDF)
    with pytest.raises(DomainValidationError, match="Mais de um PDF"):
        import_quadra_pdfs(tmp_path / "other-maps", source)
    assert not (tmp_path / "other-maps").exists()


def test_map_routes_are_exposed_without_municipal_cadastro_requests() -> None:
    """Expose read-only PDF download and map catalog endpoints; uploads are CLI-only."""
    schema = create_app().openapi()
    assert "/api/maps" in schema["paths"]
    assert set(schema["paths"]["/api/maps/quadras/{quadra}/pdf"]) == {"get"}


def test_imported_pdfs_are_served_from_configured_maps_dir(tmp_path: Path) -> None:
    """CLI import and the read-only routes share `maps_dir`, which no longer derives from a DB file."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "0928.pdf").write_bytes(_PDF)
    maps_dir = tmp_path / "volume" / "maps"
    settings = AppSettings(maps_dir=maps_dir)
    import_quadra_pdfs(settings.maps_dir, source)
    assert get_map_catalog(settings.maps_dir).local_quadras == (928,)
    assert get_quadra_pdf(settings.maps_dir, 928) == maps_dir / "quadras" / "0928.pdf"


@pytest.mark.asyncio
async def test_public_hardening_headers_docs_disabled_and_pdf_serving(tmp_path: Path) -> None:
    """Verify CSP, security headers, Cache-Control on UI/static, disabled docs/redoc, and PDF responses."""
    maps_dir = tmp_path / "maps"
    save_quadra_pdf(maps_dir, 928, _PDF)
    app = create_app(AppSettings(maps_dir=maps_dir, auto_sync_enabled=False))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        index_res = await client.get("/")
        assert index_res.status_code == 200
        assert index_res.headers["Content-Security-Policy"] == _EXPECTED_CSP
        assert index_res.headers["X-Content-Type-Options"] == "nosniff"
        assert index_res.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert index_res.headers["Cache-Control"] == "no-cache"
        html = index_res.text
        assert 'href="static/styles.css"' in html
        assert 'src="static/app.js"' in html
        assert "/static/" not in html
        assert "/docs" not in html
        assert "API Docs" not in html
        assert 'style="' not in html

        for asset in ("/static/styles.css", "/static/app.js", "/static/api.js"):
            asset_res = await client.get(asset)
            assert asset_res.status_code == 200
            assert asset_res.headers["Content-Security-Policy"] == _EXPECTED_CSP
            assert asset_res.headers["X-Content-Type-Options"] == "nosniff"
            assert asset_res.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
            assert asset_res.headers["Cache-Control"] == "no-cache"

        for disabled_path in ("/docs", "/docs/oauth2-redirect", "/redoc"):
            doc_res = await client.get(disabled_path)
            assert doc_res.status_code == 404
            assert doc_res.headers["Content-Security-Policy"] == _EXPECTED_CSP
            assert doc_res.headers["X-Content-Type-Options"] == "nosniff"
            assert doc_res.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"

        openapi_res = await client.get("/openapi.json")
        assert openapi_res.status_code == 200
        assert openapi_res.headers["Content-Security-Policy"] == _EXPECTED_CSP
        assert openapi_res.headers["X-Content-Type-Options"] == "nosniff"
        assert openapi_res.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert "/api/properties" in openapi_res.json()["paths"]

        maps_res = await client.get("/api/maps", params={"quadra": 928})
        assert maps_res.status_code == 200
        assert maps_res.headers["Content-Security-Policy"] == _EXPECTED_CSP
        assert maps_res.headers["X-Content-Type-Options"] == "nosniff"
        assert maps_res.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert maps_res.headers.get("Cache-Control") != "no-cache"
        assert maps_res.json()["selected"]["local_pdf"] is True

        pdf_res = await client.get("/api/maps/quadras/928/pdf")
        assert pdf_res.status_code == 200
        assert pdf_res.content == _PDF
        assert pdf_res.headers["content-type"] == "application/pdf"
        assert pdf_res.headers["Content-Disposition"] == 'inline; filename="quadra-0928.pdf"'
        assert pdf_res.headers["Content-Security-Policy"] == _EXPECTED_CSP
        assert pdf_res.headers["X-Content-Type-Options"] == "nosniff"
        assert pdf_res.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"

        missing_pdf = await client.get("/api/maps/quadras/1/pdf")
        assert missing_pdf.status_code == 404
        assert missing_pdf.headers["Content-Security-Policy"] == _EXPECTED_CSP
        assert missing_pdf.headers["X-Content-Type-Options"] == "nosniff"


def test_web_assets_avoid_inline_styles_and_work_under_stripped_proxy_prefix() -> None:
    """Ensure JS/HTML assets have no inline styles or root-pinned paths and preserve external URLs."""
    app_js = (_WEB_DIR / "app.js").read_text(encoding="utf-8")
    api_js = (_WEB_DIR / "api.js").read_text(encoding="utf-8")

    assert "style:" not in app_js.replace('style: "currency"', "").replace(
        'dateStyle: "short"', ""
    ).replace('timeStyle: "short"', "")
    assert 'setAttribute("style"' not in app_js
    assert '"/api/' not in app_js
    assert "`/api/" not in app_js
    assert "`api/maps/quadras/${selected.quadra}/pdf?v=${Date.now()}`" in app_js
    assert '"/api/' not in api_js
    assert "`/api/" not in api_js

    node = shutil.which("node")
    assert node is not None, "node is required to verify frontend ES modules"
    script = """
import {
  fetchAgencies,
  fetchFacets,
  fetchMapCatalog,
  fetchProperties,
  fetchPropertyDetail,
  fetchStats,
  fetchSyncStatus,
  searchProperties,
} from "./src/imob/web/api.js";
import { safeHttpUrl } from "./src/imob/web/app.js";

const requested = [];
globalThis.window = {
  location: {
    href: "https://host.example/imob/?purpose=aluguel",
    origin: "https://host.example",
    pathname: "/imob/",
  },
  setTimeout: (fn, ms) => setTimeout(fn, ms),
  clearTimeout: (id) => clearTimeout(id),
  fetch: async (url) => {
    requested.push(url);
    return {
      ok: true,
      status: 200,
      text: async () => JSON.stringify({
        active_properties: 0,
        inactive_properties: 0,
        rent_count: 0,
        sale_count: 0,
        both_count: 0,
        agencies_total: 1,
        agencies_synced: 1,
        price_drops_count: 0,
        duplicate_groups_count: 0,
        similarity_review_pairs_count: 0,
        last_global_sync_at: null,
        property_types: [],
        neighborhoods: [],
        agencies: [],
        purposes: [],
        total: 0,
        page: 1,
        page_size: 30,
        total_pages: 0,
        items: [],
        is_running: false,
        completed_agencies: 0,
        total_agencies: 1,
        active_agencies: [],
        auto_sync_enabled: true,
        sync_schedule: ["06:00", "18:00"],
        sync_timezone: "America/Sao_Paulo",
        next_scheduled_run_at: null,
        source_url: "https://patobranco.pr.gov.br/mapas-e-arquivos-para-download/",
        ranges: [],
        resources: [],
        local_quadras: [],
        selected: null,
      }),
    };
  },
};

await fetchStats();
await fetchFacets("aluguel");
await fetchProperties(new URLSearchParams("page=2"));
await searchProperties({ page: 1 });
await fetchSyncStatus();
await fetchMapCatalog(928);

const resolvedUnderPrefix = requested.map((p) => new URL(p, globalThis.window.location.href).href);
const externalAgency = safeHttpUrl("https://arcariimoveis.imb.br/imovel/101");
const externalPhoto = safeHttpUrl("http://edersonimoveis.com.br/fotos/capa.jpg");
const protocolRelative = safeHttpUrl("//cdn.imobiliaria.com.br/foto.jpg");
const relativeMedia = safeHttpUrl("/media/foto.jpg");
const relativeStatic = safeHttpUrl("static/placeholder.png");
const relativeMapPdf = safeHttpUrl("api/maps/quadras/928/pdf?v=1");
const unsafeScheme = safeHttpUrl("javascript:alert(1)");

globalThis.window.location.href = "https://host.example/imob";
globalThis.window.location.pathname = "/imob";
const mediaWithoutTrailingSlash = safeHttpUrl("/media/foto.jpg");

console.log(JSON.stringify({
  requested,
  resolvedUnderPrefix,
  externalAgency,
  externalPhoto,
  protocolRelative,
  relativeMedia,
  relativeStatic,
  relativeMapPdf,
  unsafeScheme,
  mediaWithoutTrailingSlash,
}));
"""
    proc = subprocess.run(
        [node, "--input-type=module", "-e", script],
        cwd=_WEB_DIR.parent.parent.parent,
        check=True,
        capture_output=True,
        text=True,
    )
    out = json.loads(proc.stdout)
    assert out["requested"] == [
        "api/stats",
        "api/facets?purpose=aluguel",
        "api/properties?page=2",
        "api/properties/search",
        "api/sync/status",
        "api/maps?quadra=928",
    ]
    assert all(url.startswith("https://host.example/imob/api/") for url in out["resolvedUnderPrefix"])
    assert out["externalAgency"] == "https://arcariimoveis.imb.br/imovel/101"
    assert out["externalPhoto"] == "http://edersonimoveis.com.br/fotos/capa.jpg"
    assert out["protocolRelative"] == "https://cdn.imobiliaria.com.br/foto.jpg"
    assert out["relativeMedia"] == "https://host.example/imob/media/foto.jpg"
    assert out["relativeStatic"] == "https://host.example/imob/static/placeholder.png"
    assert out["relativeMapPdf"] == "https://host.example/imob/api/maps/quadras/928/pdf?v=1"
    assert out["unsafeScheme"] is None
    assert out["mediaWithoutTrailingSlash"] == "https://host.example/imob/media/foto.jpg"

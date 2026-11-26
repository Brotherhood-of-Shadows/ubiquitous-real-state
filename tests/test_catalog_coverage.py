"""Registry coverage and pagination regressions for the new catalog sources."""

from __future__ import annotations

from collections import Counter

import httpx
import pytest

from imob.api.app import create_app
from imob.domain.enums import ListingPurpose, PlatformKind, SyncStatus
from imob.domain.normalizer import normalize_scraped_item
from imob.domain.registry import AGENCIES, AGENCIES_BY_SLUG
from imob.scrapers import PlatformScraperRegistry
from imob.scrapers.http_client import PoliteHttpClient
from imob.scrapers.widesys import parse_widesys_listing_html
from tests.conftest import MySqlTestDatabase


def test_registry_includes_new_sources_once_with_catalog_engines() -> None:
    """Keep the requested sources, unique domains, and implemented engines in the registry."""
    added = {
        "esatta",
        "tradicao",
        "cattuci",
        "veronese",
        "duo",
        "mattia",
        "america",
        "acacio",
        "imperador",
        "mazorana",
        "pauloreis",
        "valen",
        "cristian",
        "familiar",
        "fortun",
        "alvaro",
        "carlos",
        "negociar",
        "magcen",
        "morosini",
        "popiel",
        "cad",
        "mariely",
        "junior",
    }
    public_html = {a.slug for a in AGENCIES if a.platform == PlatformKind.PUBLIC_HTML}
    counts = Counter(a.platform for a in AGENCIES)

    assert added <= AGENCIES_BY_SLUG.keys()
    assert len(AGENCIES) == len(AGENCIES_BY_SLUG) == len({a.base_url for a in AGENCIES}) == 54
    assert public_html == {"valen", "cristian", "familiar", "alvaro", "carlos", "mariely", "junior"}
    assert all(a.entry_urls for a in AGENCIES)
    assert counts == {
        PlatformKind.WIDESYS: 25,
        PlatformKind.IMONOV: 18,
        PlatformKind.PUBLIC_HTML: 7,
        PlatformKind.AGIL: 1,
        PlatformKind.APOLAR: 1,
        PlatformKind.UNIQ: 1,
        PlatformKind.MORETTI: 1,
    }
    assert AGENCIES_BY_SLUG["mattia"].entry_urls == (
        (ListingPurpose.SALE, "https://mattiacorretores.com.br/imoveis-venda"),
    )


@pytest.mark.asyncio
async def test_html_source_is_visible_and_syncs_through_the_real_service(
    mysql_db: MySqlTestDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise registry wiring, HTML parsing, normalization, persistence, and API stats together."""

    async def post_text(
        self: PoliteHttpClient,
        url: str,
        *,
        agency_slug: str,
        form_data: dict[str, str],
    ) -> tuple[int, str, str]:
        assert url == "https://valenimobiliaria.com.br/pesquisa"
        assert form_data == {"cidade": "Pato Branco"}
        return (
            200,
            url,
            """<section><article><a href="/imovel/81/casa-pato-branco">
          <h3>Casa em Pato Branco</h3>À venda R$ 450.000,00</a></article></section>""",
        )

    monkeypatch.setattr(PoliteHttpClient, "post_text", post_text)
    app = create_app(mysql_db.settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        before = (await client.get("/api/agencies")).json()
        report = await app.state.sync_service.sync_agencies(agency_slugs=("valen",), force=True)
        after = (await client.get("/api/agencies")).json()
        stats = (await client.get("/api/stats")).json()

    assert len(before) == len(after) == stats["agencies_total"] == len(AGENCIES)
    assert report.agencies[0].status == SyncStatus.SUCCESS
    assert report.total_requests == 1
    assert report.total_inserted == 1
    valen = next(a for a in after if a["slug"] == "valen")
    assert valen["last_sync_status"] == "success"
    assert valen["last_error"] is None
    assert stats["agencies_synced"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", [PlatformKind.WIDESYS, PlatformKind.IMONOV])
@pytest.mark.parametrize(
    "html, has_errors",
    [
        ("<html><h1>Site em manutenção</h1></html>", True),
        ("<p>Nenhum imóvel encontrado.</p>", False),
        ("<p><span>0</span> opções encontrado(s)</p>", False),
        ("<script>const message = 'Nenhum imóvel';</script><h1>Novo layout</h1>", True),
    ],
)
async def test_unknown_catalog_does_not_look_like_successful_empty_sync(
    monkeypatch: pytest.MonkeyPatch,
    platform: PlatformKind,
    html: str,
    has_errors: bool,
) -> None:
    """Distinguish explicit empty results from errors or unsupported HTML layouts."""
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=html))
    )

    def make_client(**kwargs: object) -> httpx.AsyncClient:
        return client

    monkeypatch.setattr(httpx, "AsyncClient", make_client)
    original = AGENCIES_BY_SLUG["mattia" if platform == PlatformKind.WIDESYS else "america"]
    async with PoliteHttpClient() as http:
        scraper = PlatformScraperRegistry(http).get_scraper(platform)
        actual = await scraper.fetch_catalog(original, max_pages=2)

    assert (actual.items, actual.requests_made, actual.had_section_errors) == ((), 1, has_errors)


def _card(external_id: int, status: str) -> str:
    return f"""<div class="featured-thumb"><span class="sale">Venda</span><em>{status}</em>
      <h5>Apartamento</h5><a href="/{external_id}-apartamento-centro">
      <div class="product-title">Apartamento no Centro</div></a>
      <div class="product-address">Centro - Pato Branco/PR</div></div>"""


@pytest.mark.asyncio
async def test_widesys_keeps_paginating_past_sold_cards_and_includes_price_on_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unavailable cards count for pagination but cannot enter the active property catalog."""
    offsets: list[str] = []
    first_page = '<a href="?start=3">2</a>' + "".join(_card(i, "VENDIDA") for i in range(1, 4))

    def handle(request: httpx.Request) -> httpx.Response:
        offset = request.url.params["start"]
        offsets.append(offset)
        assert offset in {"0", "3"}
        return httpx.Response(200, text=first_page if offset == "0" else _card(4, "Disponível"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))

    def make_client(**kwargs: object) -> httpx.AsyncClient:
        return client

    monkeypatch.setattr(httpx, "AsyncClient", make_client)
    agency = AGENCIES_BY_SLUG["mattia"]
    async with PoliteHttpClient(per_domain_delay_seconds=0) as http:
        scraper = PlatformScraperRegistry(http).get_scraper(agency.platform)
        actual = await scraper.fetch_catalog(agency, max_pages=4)
    normalized = [normalize_scraped_item(item, agency) for item in actual.items]
    accepted = [item for item in normalized if item is not None]

    assert offsets == ["0", "3"]
    assert [item.is_available for item in actual.items] == [False, False, False, True]
    assert len(accepted) == 1
    assert (accepted[0].external_id, accepted[0].price_on_request) == ("4", True)


def test_legacy_card_status_and_prices_do_not_leak_from_neighbor() -> None:
    """A sold neighbor must not mark an available property sold or supply its price."""
    html = """<div>
      <div><span>VENDIDO</span><a data-action="favorito" data-id="1"
        data-link="/1-casa-centro" data-name="Casa" data-price="R$ 400.000,00"></a></div>
      <div><span>Não locado</span><a class="price">R$ 500.000,00</a>
        <a data-action="favorito" data-id="2" data-link="/2-casa-centro" data-name="Casa"></a></div>
    </div>"""
    items, _ = parse_widesys_listing_html(
        html,
        agency_slug="tradicao",
        base_url="https://tradicaoimoveispb.com.br",
        default_purpose=ListingPurpose.SALE,
    )
    assert [(item.external_id, item.is_available, item.sale_price) for item in items] == [
        ("1", False, 400000.0),
        ("2", True, 500000.0),
    ]


@pytest.mark.asyncio
async def test_widesys_preserves_search_filters_and_reports_http_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed sales section must not appear complete, and pagination must retain its search filters."""
    agency = AGENCIES_BY_SLUG["duo"].model_copy(
        update={
            "entry_urls": (
                (ListingPurpose.RENT, "https://duoimoveispb.com.br/imoveis-locacao"),
                (
                    ListingPurpose.SALE,
                    "https://duoimoveispb.com.br/busca?filter[categoria_catid]=-40"
                    "&filter[endereco_cid][]=1",
                ),
            ),
        }
    )

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/imoveis-locacao":
            return httpx.Response(200, text="Nenhum imóvel encontrado")
        assert request.url.params["filter[categoria_catid]"] == "-40"
        assert request.url.params["filter[endereco_cid][]"] == "1"
        assert request.url.params["start"] == "0"
        return httpx.Response(500, text="Site temporariamente indisponível")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))

    def make_client(**kwargs: object) -> httpx.AsyncClient:
        return client

    monkeypatch.setattr(httpx, "AsyncClient", make_client)
    async with PoliteHttpClient(per_domain_delay_seconds=0, max_retries=0) as http:
        result = (
            await PlatformScraperRegistry(http)
            .get_scraper(agency.platform)
            .fetch_catalog(agency, max_pages=2)
        )
    assert result.had_section_errors
    assert result.error_summary == (
        "HTTP 500 at https://duoimoveispb.com.br/busca?filter%5Bcategoria_catid%5D=-40"
        "&filter%5Bendereco_cid%5D%5B%5D=1&start=0"
    )


@pytest.mark.asyncio
async def test_duo_uses_city_sales_page_and_stops_at_empty_paginator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crowded single-page catalog is complete; keep separately labeled sale and rental prices."""
    agency = AGENCIES_BY_SLUG["duo"]
    paths: list[str] = []
    dual_card = """<div class="featured-thumb"><span class="sale">Aluguel , Venda</span>
      <a href="/imoveis-locacao/1001-apartamento-centro-pato-branco">Apartamento em Pato Branco</a>
      <div class="price"><span title="Aluguel">CONSULTE /mês</span>
      <span title="Venda">R$ 1.100.000,00</span></div></div>"""

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.params == httpx.QueryParams({"start": "0"})
        path = request.url.path
        paths.append(path)
        if path == "/imoveis-locacao":
            cards = dual_card
        else:
            assert path == "/imoveis/imoveis-a-venda/ac/pato-branco"
            cards = dual_card + "".join(_card(i, "Disponível") for i in range(1, 14))
        return httpx.Response(200, text=cards + '<div class="pagination"></div>')

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
    async with PoliteHttpClient(per_domain_delay_seconds=0) as http:
        scraper = PlatformScraperRegistry(http).get_scraper(agency.platform)
        result = await scraper.fetch_catalog(agency, max_pages=3)

    assert paths == ["/imoveis-locacao", "/imoveis/imoveis-a-venda/ac/pato-branco"]
    assert not result.had_section_errors
    assert (result.requests_made, len(result.items)) == (2, 15)
    dual_items = [item for item in result.items if item.external_id == "1001"]
    assert len(dual_items) == 2
    for item in dual_items:
        normalized = normalize_scraped_item(item, agency)
        assert normalized is not None
        assert (normalized.purpose, normalized.sale_price, normalized.rent_price) == (
            ListingPurpose.BOTH,
            1_100_000,
            None,
        )

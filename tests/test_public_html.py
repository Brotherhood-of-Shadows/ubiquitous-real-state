"""Semantic catalog parsing and bounded HTTP traversal for the six HTML sources."""

from __future__ import annotations

import httpx
import pytest

from imob.domain.enums import ListingPurpose
from imob.domain.normalizer import normalize_scraped_item
from imob.domain.registry import AGENCIES_BY_SLUG
from imob.scrapers.http_client import PoliteHttpClient
from imob.scrapers.public_html import PublicHtmlScraper, parse_public_catalog_html


@pytest.mark.parametrize(
    "slug, path, title, purpose",
    [
        ("valen", "/imovel/81/casa-pato-branco", "Casa em Pato Branco", ListingPurpose.SALE),
        ("cristian", "/imovel/1081/sobrado-pato-branco", "Sobrado em Pato Branco", ListingPurpose.SALE),
        (
            "familiar",
            "/imovel/AP0021/apartamento-centro-pato-branco/aluguel",
            "Apartamento em Pato Branco",
            ListingPurpose.RENT,
        ),
        ("alvaro", "/ver-imovel/1403-residencia-no-centro", "Casa em Pato Branco", ListingPurpose.SALE),
        ("carlos", "/imovel/3671742/casa-venda-pato-branco-pr", "Casa em Pato Branco", ListingPurpose.SALE),
    ],
)
def test_property_url_formats_produce_city_filtered_listings(
    slug: str,
    path: str,
    title: str,
    purpose: ListingPurpose,
) -> None:
    """Handle different wrappers and link attributes without depending on guessed CSS classes."""
    agency = AGENCIES_BY_SLUG[slug]
    html = f'''<section><article data-url="{path}">
      <a href="{path}"><img src="/fotos/property.jpg" /></a><h3>{title}</h3>
      <a href="{path}">Detalhes</a><p>R$ 2.400,00</p><p>Condomínio: R$ 200,00 | IPTU: R$ 30,00</p>
      <p>262,73 m² área do lote 155 m² área construída 80,50 m² área privativa</p>
      <p>3 quartos (1 suíte) 2 banheiros 2 vagas</p></article></section>'''
    if slug == "familiar":
        html = html.replace(f'data-url="{path}"', f'''onclick="location.href='{path}'"''')
    items, next_url, unresolved = parse_public_catalog_html(
        html,
        agency_slug=slug,
        base_url=agency.base_url,
        page_url=agency.entry_urls[0][1],
        default_purpose=purpose,
    )
    assert len(items) == 1
    item = items[0]
    assert item.source_url == agency.base_url + path
    assert (item.title, item.raw_city, item.raw_purpose) == (title, "Pato Branco", purpose.value)
    assert (item.bedrooms, item.suites, item.bathrooms, item.parking_spots) == (3, 1, 2, 2)
    assert (item.area_total_m2, item.area_built_m2, item.area_private_m2) == (262.73, 155.0, 80.5)
    assert (item.condo_fee, item.iptu_fee) == (200, 30)
    assert (item.sale_price, item.rent_price) == (
        (None, 2400) if purpose == ListingPurpose.RENT else (2400, None)
    )
    assert item.image_urls == (agency.base_url + "/fotos/property.jpg",)
    assert normalize_scraped_item(item, agency) is not None
    assert (next_url, unresolved) == (None, False)


def test_neighbor_prices_footer_city_and_foreign_links_cannot_contaminate_cards() -> None:
    """Preserve unknown locations and price-less properties while rejecting other cities and domains."""
    agency = AGENCIES_BY_SLUG["cristian"]
    html = """<main><article><a href="/imovel/1/apartamento"><h3>Apartamento sem localização</h3>
      Consulte-nos</a></article><article><a href="/imovel/2/apartamento-cascavel">
      <h3>Apartamento em Cascavel</h3></a><p>Centro - Cascavel/PR R$ 500.000,00</p></article>
      <article><a href="/imovel/3/apartamento-pato-branco"><h3>Apartamento em Pato Branco</h3>
      Consulte-nos</a><span>VENDIDO</span></article>
      <article><a href="/imovel/4/apartamento-pato-branco"><h3>Apartamento em Pato Branco</h3>
      Consulte-nos</a></article>
      <article><a href="https://other.example/imovel/5/falso">Pato Branco R$ 10.000,00</a></article>
      </main><footer>Pato Branco/PR R$ 900.000,00</footer>"""
    items, _, _ = parse_public_catalog_html(
        html,
        agency_slug=agency.slug,
        base_url=agency.base_url,
        page_url=agency.entry_urls[0][1],
        default_purpose=ListingPurpose.SALE,
    )
    assert [(item.external_id, item.sale_price) for item in items] == [
        ("1", None),
        ("2", 500000),
        ("3", None),
        ("4", None),
    ]
    normalized = [normalize_scraped_item(item, agency) for item in items]
    assert [item.external_id for item in normalized if item is not None] == ["4"]
    assert items[0].raw_city is None
    assert items[1].raw_city == "Cascavel"


@pytest.mark.parametrize(
    "slug, page_key", [("cristian", "pagina"), ("familiar", "pagina"), ("carlos", "pag")]
)
@pytest.mark.asyncio
async def test_follow_actual_pagination_and_report_truncation(
    monkeypatch: pytest.MonkeyPatch,
    slug: str,
    page_key: str,
) -> None:
    """Traverse the published query parameters without dropping the second page or exceeding the cap."""
    agency = AGENCIES_BY_SLUG[slug]
    pages: list[str] = []
    agency = agency.model_copy(update={"entry_urls": (agency.entry_urls[0],)})

    def handle(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get(page_key, "1")
        pages.append(page)
        card = (
            f'<article><a href="/imovel/{page}/casa-pato-branco"><h3>Casa em Pato Branco</h3></a></article>'
        )
        more = f'<a href="?{page_key}=2">Próxima</a>' if page == "1" else ""
        return httpx.Response(200, text=f"<main>{card}{more}</main>")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))

    def make_client(**kwargs: object) -> httpx.AsyncClient:
        return client

    monkeypatch.setattr(httpx, "AsyncClient", make_client)
    async with PoliteHttpClient(per_domain_delay_seconds=0) as http:
        result = await PublicHtmlScraper(http).fetch_catalog(agency, max_pages=2)
    assert pages == ["1", "2"]
    assert [item.external_id for item in result.items] == ["1", "2"]
    assert (result.requests_made, result.had_section_errors) == (2, False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode, expected_requests", [("limit", 1), ("repeated", 2), ("javascript", 1), ("unknown", 1)]
)
async def test_incomplete_catalogs_are_explicit_errors(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected_requests: int,
) -> None:
    """Prevent incomplete catalogs from enabling property deactivation or claiming complete success."""
    agency = AGENCIES_BY_SLUG["cristian"]
    card = (
        '<article><a href="/imovel/133/venda/apartamento-em-pato-branco">'
        "Apartamento em Pato Branco</a></article>"
    )
    if mode == "unknown":
        html = "<p>Serviço temporariamente indisponível</p>"
    elif mode == "javascript":
        html = card + '<button onclick="loadMore()">Ver mais</button>'
    else:
        html = card + '<a href="?pagina=2">Próxima</a>'
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=html))
    )

    def make_client(**kwargs: object) -> httpx.AsyncClient:
        return client

    monkeypatch.setattr(httpx, "AsyncClient", make_client)
    async with PoliteHttpClient() as http:
        result = await PublicHtmlScraper(http).fetch_catalog(agency, max_pages=1 if mode == "limit" else 5)
    assert result.had_section_errors
    assert result.error_summary
    assert result.requests_made == expected_requests


def test_alvaro_sections_keep_both_transactions_for_the_same_external_id() -> None:
    """The caller must receive sale and rental observations for the repository to reconcile."""
    agency = AGENCIES_BY_SLUG["alvaro"]
    html = """<article><a href="/ver-imovel/1403-residencia-no-centro">
      <h3>Pato Branco - Centro</h3></a><p>R$ 2.300,00</p></article>"""
    rent_items, _, _ = parse_public_catalog_html(
        html,
        agency_slug=agency.slug,
        base_url=agency.base_url,
        page_url=agency.entry_urls[0][1],
        default_purpose=ListingPurpose.RENT,
    )
    sale_items, _, _ = parse_public_catalog_html(
        html,
        agency_slug=agency.slug,
        base_url=agency.base_url,
        page_url=agency.entry_urls[1][1],
        default_purpose=ListingPurpose.SALE,
    )
    assert (rent_items[0].external_id, sale_items[0].external_id) == ("1403", "1403")
    assert (rent_items[0].rent_price, sale_items[0].sale_price) == (2300, 2300)


def test_base_href_and_imobibrasil_pagination_are_resolved_as_catalog_controls() -> None:
    """Respect HTML base URLs and distinguish /imovel/venda/ pagination from numeric property IDs."""
    agency = AGENCIES_BY_SLUG["alvaro"]
    html = """<base href="https://www.alvaroimoveis.imb.br/" />
      <article><a href="ver-imovel/1403-residencia"><h3>Pato Branco - Centro</h3></a></article>
      <a href="/imovel/venda/?pag=2">Próxima</a>"""
    items, next_url, _ = parse_public_catalog_html(
        html,
        agency_slug=agency.slug,
        base_url=agency.base_url,
        page_url=agency.entry_urls[0][1],
        default_purpose=ListingPurpose.RENT,
    )
    assert [(item.external_id, item.source_url) for item in items] == [
        ("1403", "https://www.alvaroimoveis.imb.br/ver-imovel/1403-residencia"),
    ]
    assert next_url == "https://www.alvaroimoveis.imb.br/imovel/venda/?pag=2"


@pytest.mark.asyncio
async def test_alvaro_new_catalog_follows_pager_and_reads_slug_cards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read all current sections, including slug cards and cities outside Pato Branco."""
    agency = AGENCIES_BY_SLUG["alvaro"]
    visits: list[str] = []

    def card(slug: str, purpose: str, location: str, price: str) -> str:
        return f"""<article class="pcard"><a class="pcard__link" href="/imovel/{slug}">
          <img src="/uploads/casa.jpeg"><span class="pcard__tag">Casa</span>
          <span class="pcard__tag pcard__tag--purpose">{purpose}</span>
          <p class="pcard__price">{price}</p><h3 class="pcard__title">Casa Azul</h3>
          <p class="pcard__loc">{location}</p><ul class="pcard__facts">
          <li title="Quartos">3</li><li title="Banheiros">2</li><li title="Vagas">1</li>
          <li title="Área">138,50 m²</li></ul></a></article>"""

    def handle(request: httpx.Request) -> httpx.Response:
        visits.append(request.url.raw_path.decode())
        if request.url.path == "/locacao":
            html = card("casa-azul", "Locação", "Centro, Pato Branco", "R$ 2.400,00 /mês")
        elif request.url.path == "/venda":
            html = card("casa-verde", "Venda", "Pato Branco", "R$ 450.000,00")
            html += """<nav aria-label="Paginação" class="pager">
              <a aria-label="Próxima página" href="/imoveis?finalidade=venda&amp;page=2"></a>
            </nav>"""
        elif request.url.path == "/venda-locacao":
            html = card(
                "casa-amarela", "Venda/Locação", "Centro, Pato Branco", "R$ 3.500,00 <small>/mês</small>"
            )
        else:
            assert request.url.path == "/imoveis"
            assert request.url.params == httpx.QueryParams({"finalidade": "venda", "page": "2"})
            html = card("casa-amarela-2", "Venda", "Centro, Vitorino", "R$ 340.000,00")
        return httpx.Response(200, text=html + '<nav><a href="/imovel/venda/">Venda</a></nav>')

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
    async with PoliteHttpClient(per_domain_delay_seconds=0) as http:
        result = await PublicHtmlScraper(http).fetch_catalog(agency, max_pages=2)

    assert visits == ["/locacao", "/venda", "/imoveis?finalidade=venda&page=2", "/venda-locacao"]
    assert (result.requests_made, result.had_section_errors) == (4, False)
    rent, sale, outside, dual = result.items
    assert rent.source_url == f"{agency.base_url}/imovel/casa-azul"
    assert (rent.external_id, rent.title, rent.raw_type) == ("casa-azul", "Casa Azul", "Casa")
    assert (rent.raw_city, rent.raw_neighborhood, rent.rent_price) == ("Pato Branco", "Centro", 2400)
    assert (rent.bedrooms, rent.bathrooms, rent.parking_spots, rent.area_total_m2) == (3, 2, 1, 138.5)
    assert rent.image_urls == (f"{agency.base_url}/uploads/casa.jpeg",)
    assert (sale.raw_city, sale.raw_neighborhood, sale.sale_price) == ("Pato Branco", None, 450_000)
    assert normalize_scraped_item(rent, agency) is not None
    assert normalize_scraped_item(sale, agency) is not None
    assert outside.raw_city == "Vitorino"
    assert normalize_scraped_item(outside, agency) is None
    mixed = normalize_scraped_item(dual, agency)
    assert mixed is not None
    assert (mixed.purpose, mixed.sale_price, mixed.rent_price) == (ListingPurpose.BOTH, None, 3500)


def test_category_after_sale_badge_does_not_become_a_city() -> None:
    """Use the verified city-filter context without turning 'Casa - Bairro' into a city name."""
    agency = AGENCIES_BY_SLUG["valen"]
    html = """<article><span>À venda</span><a href="/imovel/82/casa-parque-do-som">
      <h3>Casa - Parque do Som</h3></a><p>R$ 450.000,00</p></article>"""
    items, _, _ = parse_public_catalog_html(
        html,
        agency_slug=agency.slug,
        base_url=agency.base_url,
        page_url=agency.entry_urls[0][1],
        default_purpose=ListingPurpose.SALE,
    )
    assert items[0].raw_city is None
    assert normalize_scraped_item(items[0], agency) is not None


def test_mariely_show_slug_and_junior_window_imoveis_parse_and_normalize() -> None:
    """Extract city/neighborhood from /show/ slugs and parse static window.IMOVEIS arrays."""
    mariely = AGENCIES_BY_SLUG["mariely"]
    show_href = "/show/1806/LOTE-S%C3%83%C2%83O-LUIZ-Pato-Branco-PR"
    mariely_html = f"""<div class="blog-single-post">
      <a href="{show_href}"><img src="/uploads/fotos/1.com" /></a>
      <h3 class="post-title"><a class="prop-title" href="{show_href}">LOTE - Venda</a>
      <span class="marcador-ref">Identificador: 1806</span></h3>
      <div class="descricao_imoveis"><p>Bairro: São Luiz 450,00 m²</p></div>
      <h4><span>R$</span> 540.000,00</h4></div>
      <div class="small-property-list"><a href="/show/783/LOTE--Vitorino-PR">LOTE - Venda</a></div>
      <ul id="paginacao"><li data-valor="1" class="active"><a>1</a></li>
      <li data-valor="2"><a>2</a></li></ul>"""
    m_items, m_next, m_unresolved = parse_public_catalog_html(
        mariely_html,
        agency_slug=mariely.slug,
        base_url=mariely.base_url,
        page_url=mariely.entry_urls[0][1],
        default_purpose=ListingPurpose.SALE,
    )
    assert len(m_items) == 1
    assert (m_items[0].external_id, m_items[0].raw_city, m_items[0].raw_neighborhood) == (
        "1806",
        "Pato Branco",
        "São Luiz",
    )
    assert (m_items[0].sale_price, m_items[0].area_total_m2) == (540000.0, 450.0)
    assert m_next == "https://marielycattoniimoveis.com.br/imoveis?pagina=2"
    assert not m_unresolved
    assert normalize_scraped_item(m_items[0], mariely) is not None

    junior = AGENCIES_BY_SLUG["junior"]
    junior_js = """window.IMOVEIS = [{
      codigo: "JG-0004",
      titulo: "Casa Térrea Moderna — Bairro Fraron",
      tipo: "Alto padrão",
      bairro: "Bairro Fraron",
      cidade: "Pato Branco — PR",
      preco: "R$ 990.000",
      precoLabel: "Valor",
      area: "146 m²",
      detalhe: "1 suíte + 2 quartos",
      capa: "assets/cover.jpg",
      pagina: "imovel-casa-fraron.html",
      galeria: ["assets/1.jpg"],
      descricao: ["Casa térrea moderna."],
      specs: [["Área construída", "146 m²"], ["Terreno", "291,75 m²"], ["Garagem", "02 carros"]]
    }];"""
    j_items, j_next, j_unresolved = parse_public_catalog_html(
        junior_js,
        agency_slug=junior.slug,
        base_url=junior.base_url,
        page_url=junior.entry_urls[0][1],
        default_purpose=ListingPurpose.SALE,
    )
    assert len(j_items) == 1
    assert (j_items[0].external_id, j_items[0].sale_price, j_items[0].parking_spots) == (
        "JG-0004",
        990000.0,
        2,
    )
    assert (j_items[0].area_built_m2, j_items[0].area_total_m2) == (146.0, 291.75)
    assert (j_next, j_unresolved) == (None, False)
    assert normalize_scraped_item(j_items[0], junior) is not None

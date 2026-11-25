"""Unit tests for all 5 real-estate platform HTML/JSON parsers."""

from __future__ import annotations

from imob.domain.enums import ListingPurpose
from imob.scrapers.apolar import parse_apolar_item
from imob.scrapers.imonov import parse_imonov_listing_html, set_imonov_page_number
from imob.scrapers.moretti import parse_moretti_template_html
from imob.scrapers.parsing import parse_brl
from imob.scrapers.uniq import parse_uniq_cards_html
from imob.scrapers.widesys import parse_widesys_listing_html


def test_parse_brl_prefers_currency_symbol_over_leading_unit_numbers() -> None:
    """Verify `parse_brl` extracts R$ amounts rather than apartment/highway numbers."""
    fixtures = (
        "Apartamento 201 no Centro - R$ 2.150,00",
        "BR 158 - Sob Consulta",
        "3.200,00",
    )
    actual = tuple(parse_brl(f) for f in fixtures)
    expected = (2150.0, None, 3200.0)
    assert actual == expected


def test_parse_widesys_modern_and_legacy_cards() -> None:
    """Verify Widesys parser extracts both Modern `.featured-thumb` and Legacy Bootstrap 3 cards."""
    modern_html = """
    <html><body>
      <a href="/imoveis-locacao?start=21">2</a>
      <div class="featured-thumb">
        <span class="sale">Para Alugar</span>
        <div class="price">R$ 2.300,00</div>
        <a class="starmark" data-id="884"></a>
        <div class="carousel-inner">
          <img src="/uploads/imovel884.jpg" title="Apartamento no Fraron" />
        </div>
        <div class="featured-thumb-data">
          <h5>Apartamento</h5>
          <a href="/884-apartamento-no-fraron-ap884">
            <div class="product-title">Apartamento 2 dormitórios no Fraron</div>
          </a>
          <div class="product-address">Fraron - Pato Branco/PR</div>
          <ul class="quantity">
            <li title="2 Dormitórios sendo 1 Suíte">2</li>
            <li title="Banheiros">2</li>
            <li title="Vagas de garagem">1</li>
            <li title="Área privativa">68,50 m²</li>
          </ul>
        </div>
      </div>
    </body></html>
    """
    legacy_html = """
    <html><body>
      <div class="col-sm-4">
        <img src="/fotos/legacy12.jpg" />
        <a class="price">R$ 480.000,00</a>
        <p>Sobrado excelente no bairro La Salle</p>
        <a data-action="favorito"
           data-id="12"
           data-link="/12-sobrado-la-salle-sb12"
           data-name="Sobrado no La Salle"
           data-image="/fotos/legacy12.jpg"
           data-price="R$ 480.000,00">Favoritar</a>
      </div>
    </body></html>
    """

    mod_items, step = parse_widesys_listing_html(
        modern_html,
        agency_slug="arcari",
        base_url="https://arcariimoveis.imb.br",
        default_purpose=ListingPurpose.RENT,
    )
    leg_items, _ = parse_widesys_listing_html(
        legacy_html,
        agency_slug="realizapb",
        base_url="https://realizapb.com.br",
        default_purpose=ListingPurpose.SALE,
    )

    assert step == 21
    assert len(mod_items) == 1
    assert len(leg_items) == 1

    m = mod_items[0]
    actual_mod = (
        m.external_id,
        m.ref_code,
        m.raw_purpose,
        m.raw_type,
        m.rent_price,
        m.raw_neighborhood,
        m.raw_city,
        m.bedrooms,
        m.suites,
        m.bathrooms,
        m.parking_spots,
        m.area_private_m2,
        m.image_urls,
    )
    expected_mod = (
        "884",
        "AP884",
        "aluguel",
        "Apartamento",
        2300.0,
        "Fraron",
        "Pato Branco",
        2,
        1,
        2,
        1,
        68.5,
        ("https://arcariimoveis.imb.br/uploads/imovel884.jpg",),
    )
    assert actual_mod == expected_mod

    lg = leg_items[0]
    assert (lg.external_id, lg.ref_code, lg.sale_price, lg.title) == (
        "12",
        "SB12",
        480000.0,
        "Sobrado no La Salle",
    )

    v2_html = """
    <html><body>
      <div class="property-card-modern">
        <div class="property-image-wrapper">
          <div class="badge-category">Aluguel</div>
          <button data-id="508"></button>
        </div>
        <div class="property-content">
          <div class="property-type">Sala Comercial</div>
          <a href="/imoveis-locacao/508-sala-comercial-no-bairro-sao-francisco-loc194">
            <h5 class="property-title-modern">Sala Comercial no Bairro São Francisco</h5>
          </a>
          <div class="property-features-grid">
            <div class="feature-point" title="Banheiro"><span>1</span></div>
            <div class="feature-point" title="Área"><span>47m²</span></div>
          </div>
        </div>
        <div class="property-footer-modern">
          <div class="price-entry">
            <span class="price-label">Aluguel</span>
            <span class="price-value">R$ 1.999,00</span>
          </div>
          <div class="ref-modern">REF: LOC194</div>
        </div>
      </div>
    </body></html>
    """
    v2_items, _ = parse_widesys_listing_html(
        v2_html,
        agency_slug="patoimoveis",
        base_url="https://patoimoveis.com.br",
        default_purpose=ListingPurpose.RENT,
    )
    assert len(v2_items) == 1
    v2 = v2_items[0]
    assert (v2.external_id, v2.ref_code, v2.rent_price, v2.bathrooms, v2.area_total_m2) == (
        "508",
        "LOC194",
        1999.0,
        1,
        47.0,
    )


def test_imonov_pagination_and_listing_parser_with_staging_host_and_leaflet() -> None:
    """Verify Imonov URL pagination, staging domain rewrite, R$-less prices, and Leaflet coordinates."""
    paginated_urls = (
        set_imonov_page_number(
            "https://www.habitar.imb.br/filtro/locacao/todos/pato-branco-pr/todos/0/0/0/0/0/1/ASC?v=0-0", 3
        ),
        set_imonov_page_number("https://www.assmannimoveis.com.br/filtro/venda/todos/todos/todos/0/0/1", 2),
    )
    assert paginated_urls == (
        "https://www.habitar.imb.br/filtro/locacao/todos/pato-branco-pr/todos/0/0/0/0/0/3/ASC?v=0-0",
        "https://www.assmannimoveis.com.br/filtro/venda/todos/todos/todos/0/0/2",
    )

    im_path = "/imovel/locacao/apartamento/pato-branco-pr/centro/apartamento-centro/771"
    popup = f'<a href="{im_path}"><h6>Apartamento amplo no Centro</h6></a>'
    html = f"""
    <html><body>
      <div class="property-card">
        <a href="http://imobiliariacagol1.hospedagemdesites.ws{im_path}">
          <img src="https://cdn.si9sistemas.com.br/imoveis/771/capa.jpg" />
        </a>
        <div class="property-price">Apartamento amplo no Centro</div>
        <div class="property-price">3.200,00</div>
        <span>Ref.: AP771 | A. Privativa: 92,00 m²</span>
        <div class="feature"><img src="/icons/bed.svg" /> 3</div>
        <div class="feature"><img src="/icons/car.svg" /> 2</div>
      </div>
      <script>
        L.marker([-26.2289, -52.6711]).addTo(map).bindPopup('{popup}');
      </script>
    </body></html>
    """

    items = parse_imonov_listing_html(
        html,
        agency_slug="cagolchaves",
        base_url="http://imobiliariacagolchaves.com.br",
        default_purpose=ListingPurpose.RENT,
    )
    assert len(items) == 1
    it = items[0]

    actual = (
        it.external_id,
        it.source_url,
        it.ref_code,
        it.rent_price,
        it.raw_city,
        it.raw_neighborhood,
        it.area_private_m2,
        it.bedrooms,
        it.parking_spots,
        it.latitude,
        it.longitude,
    )
    expected = (
        "771",
        "http://imobiliariacagolchaves.com.br/imovel/locacao/apartamento/pato-branco-pr/centro/apartamento-centro/771",
        "AP771",
        3200.0,
        "Pato Branco",
        "centro",
        92.0,
        3,
        2,
        -26.2289,
        -52.6711,
    )
    assert actual == expected


def test_parse_uniq_moretti_and_apolar_payloads() -> None:
    """Verify Uniq HTML, Moretti MyHome template HTML, and Apolar REST JSON parsers."""
    uniq_html = """
    <div class="LI_Imovel" id="901">
      <span class="buildingId">901</span>
      <span class="ImovelId"><span class="id">UN901</span></span>
      <span class="SubCategoria">Sobrado</span>
      <a class="Title" href="/imovel/901/sobrado-jardim-floresta">Sobrado 3 quartos Jardim Floresta</a>
      <span class="Endereco">
        <span class="Cep">CEP: 85504-000</span>
        <span class="Rua">Rua das Araucárias</span>
        <span class="Numero">Nº 150</span>
        <span class="Bairro">Jardim Floresta</span>
        <span class="cidade">Pato Branco</span>
        <span class="Estado">PR</span>
      </span>
      <div class="ImovelValor">
        <span class="ValorDesc">Aluguel</span><span class="Valor">R$ 3.500,00</span>
      </div>
      <div class="ImovelValor">
        <span class="ValorDesc">Condomínio</span><span class="Valor">R$ 220,00</span>
      </div>
      <div class="ImovelValor">
        <span class="ValorDesc">IPTU</span><span class="Valor">R$ 95,00</span>
      </div>
      <span class="ResumoItem AREA_PRIVATIVA"><span class="val">140 m²</span></span>
      <span class="ResumoItem DORMITORIOS"><span class="val">3</span></span>
      <div class="ImovelImagesSlider"><img data-src="/fotos/uniq901.jpg" /></div>
    </div>
    """
    uniq_items = parse_uniq_cards_html(
        uniq_html,
        agency_slug="uniq",
        base_url="https://www.imobiliariauniq.com.br",
        default_purpose=ListingPurpose.RENT,
    )
    assert len(uniq_items) == 1
    u = uniq_items[0]
    assert (
        u.external_id,
        u.ref_code,
        u.rent_price,
        u.condo_fee,
        u.iptu_fee,
        u.street,
        u.area_private_m2,
    ) == (
        "901",
        "UN901",
        3500.0,
        220.0,
        95.0,
        "Rua das Araucárias",
        140.0,
    )

    m_base = "https://www.imobiliariamoretti.imb.br"
    moretti_tpl = f"""
    <a class="myhome-listing-card" href="{m_base}/pt-BR/imoveis/apto-centro-ref-m550/">
      <span class="myhome-listing-card__label">Locação</span>
      <span class="myhome-listing-card__label">Apartamento</span>
      <div class="myhome-listing-card__name">Apartamento Centro - Ref. M550</div>
      <div class="myhome-listing-card-main-value">R$ 1.950 /mês</div>
      <img src="data:image/svg+xml;base64,AAAA"
           data-srcset="{m_base}/wp-content/uploads/m550-400w.jpg 400w,
                        {m_base}/wp-content/uploads/m550-1024w.jpg 1024w" />
      <div class="myhome-attribute"><div class="myhome-attribute__value">74 m²</div></div>
      <div class="myhome-attribute">
        <svg viewBox="0 0 640 512"><path d="M32 32..."/></svg>
        <div class="myhome-attribute__value">2</div>
      </div>
    </a>
    """
    moretti_items = parse_moretti_template_html(
        moretti_tpl,
        agency_slug="moretti",
        base_url="https://www.imobiliariamoretti.imb.br",
    )
    assert len(moretti_items) == 1
    mo = moretti_items[0]
    assert (
        mo.external_id,
        mo.ref_code,
        mo.raw_purpose,
        mo.rent_price,
        mo.area_total_m2,
        mo.bedrooms,
        mo.image_urls,
    ) == (
        "M550",
        "M550",
        "aluguel",
        1950.0,
        74.0,
        2,
        ("https://www.imobiliariamoretti.imb.br/wp-content/uploads/m550-1024w.jpg",),
    )

    apolar_raw = {
        "idImovel": 442211,
        "referencia_search": "442211L",
        "IsLocacao": True,
        "transacao": "Locação",
        "valorlocacao": 2100.0,
        "ValorAnterior": 2400.0,
        "vlrcondominio": 310.0,
        "iptu": 65.0,
        "tipo": "Apartamento",
        "bairro": "Centro",
        "cidade": "Pato Branco",
        "estado": "PR",
        "area_util": 66.0,
        "dormitorios": 2,
        "suites": 1,
        "banheiro": 2,
        "garagem": 1,
        "EixoX": -26.2295,
        "EixoY": -52.6718,
        "AceitaPets": True,
        "SacadacomChurrasqueira": True,
        "popup_fotos": {
            "Foto": [
                {
                    "URLArquivo": [
                        "https://cdn.apolar.com.br/442211/1.jpg",
                        "https://cdn.apolar.com.br/442211/2.jpg",
                    ]
                }
            ]
        },
    }
    ap = parse_apolar_item(apolar_raw)
    assert ap is not None
    assert (
        ap.external_id,
        ap.rent_price,
        ap.previous_price,
        ap.condo_fee,
        ap.iptu_fee,
        ap.accepts_pets,
        ap.has_barbecue,
        ap.latitude,
        len(ap.image_urls),
    ) == ("442211", 2100.0, 2400.0, 310.0, 65.0, True, True, -26.2295, 2)


def test_imonov_feature_labels_font_icons_and_superscript_area() -> None:
    """Recover labelled counts and an unlabelled m² area previously lost by the parser."""
    html = """<div class="property-card">
      <a href="/imovel/venda/apartamento/pato-branco-pr/centro/apartamento/999">Apartamento</a>
      <span>125,50 m²</span>
      <ul class="property-features">
        <li title="3 dormitórios"></li>
        <li><i class="fa fa-bath"></i>2</li>
        <li aria-label="Vagas"><i class="fa fa-car"></i>0</li>
        <li data-feature="suite">1</li>
      </ul>
    </div>"""
    items = parse_imonov_listing_html(
        html,
        agency_slug="trento",
        base_url="https://www.imobiliariatrento.com.br",
        default_purpose=ListingPurpose.SALE,
    )
    assert len(items) == 1
    actual = items[0]
    assert (
        actual.area_total_m2,
        actual.bedrooms,
        actual.suites,
        actual.bathrooms,
        actual.parking_spots,
    ) == (125.5, 3, 1, 2, 0)

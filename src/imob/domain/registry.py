"""Public catalog sources monitored in Pato Branco."""

from __future__ import annotations

from imob.domain.enums import ListingPurpose, PlatformKind
from imob.domain.models import AgencyConfig

_RENT = ListingPurpose.RENT
_SALE = ListingPurpose.SALE
_BOTH = ListingPurpose.BOTH


def _widesys(
    slug: str,
    name: str,
    base_url: str,
    *,
    has_rent: bool = True,
    has_sale: bool = True,
) -> AgencyConfig:
    entries: list[tuple[ListingPurpose, str]] = []
    if has_rent:
        entries.append((_RENT, f"{base_url}/imoveis-locacao"))
    if has_sale:
        entries.append((_SALE, f"{base_url}/imoveis-venda"))
    return AgencyConfig(
        slug=slug,
        name=name,
        base_url=base_url,
        platform=PlatformKind.WIDESYS,
        entry_urls=tuple(entries),
        default_city_assumed=True,
    )


AGENCIES: tuple[AgencyConfig, ...] = (
    # --- Widesys ---
    _widesys("arcari", "Arcari Imóveis", "https://arcariimoveis.imb.br"),
    _widesys("ederson", "Ederson Imóveis", "https://edersonimoveis.com.br"),
    _widesys("realizapb", "Imobiliária Realiza PB", "https://realizapb.com.br"),
    _widesys("casamais", "Imobiliária Casa Mais", "https://casamaisimob.com.br"),
    _widesys("rafa", "Rafa Imóveis", "https://rafaimoveis.com", has_rent=False, has_sale=True),
    _widesys("imobilli", "Imóbilli Negócios Imobiliários", "https://imobilli.imb.br"),
    _widesys("valmir", "Valmir Imóveis", "https://valmirimoveis.com.br"),
    _widesys("solar", "Solar Imóveis", "https://solar.imb.br"),
    _widesys("tomazini", "Tomazini Corretores", "https://tomaziniimoveis.com.br"),
    _widesys("morada46", "Imobiliária Morada46", "https://morada46.com.br"),
    _widesys("patoimoveis", "Pato Imóveis", "https://patoimoveis.com.br"),
    _widesys("domeneguini", "Domeneguini Imóveis", "https://domeneguiniimoveis.com.br"),
    _widesys("famex", "Famex Imobiliária", "https://fameximoveis.com.br"),
    _widesys("aliancapb", "Aliança PB Imóveis", "https://aliancapbimoveis.com.br"),
    _widesys("exclusivegold", "Exclusive Gold Imóveis", "https://exclusivegoldimoveis.com.br"),
    _widesys("favero", "Favero Imóveis", "https://faveroimoveis.com.br"),
    _widesys("esatta", "Esatta Imóveis", "https://esattaimoveis.imb.br"),
    _widesys("tradicao", "Tradição Imóveis", "https://tradicaoimoveispb.com.br"),
    _widesys("cattuci", "Cattuci Imóveis", "https://cattuciimoveis.com.br"),
    _widesys("veronese", "Veronese Imóveis", "https://imoveisveronese.com.br"),
    AgencyConfig(
        slug="duo",
        name="Duo Corretora Imobiliária",
        base_url="https://duoimoveispb.com.br",
        platform=PlatformKind.WIDESYS,
        entry_urls=(
            (_RENT, "https://duoimoveispb.com.br/imoveis-locacao"),
            (
                _SALE,
                "https://duoimoveispb.com.br/localizar/localizar-imovel?filter[categoria_catid]=-40&filter[endereco_cid][]=1",
            ),
        ),
    ),
    _widesys("mattia", "Mattia Corretores", "https://mattiacorretores.com.br", has_rent=False),
    _widesys(
        "negociar",
        "Negociar Empreendimentos Imobiliários",
        "https://negociar.app.widesys.com.br",
        has_rent=False,
    ),
    _widesys("magcen", "Magcen Imóveis", "https://magcenimoveis.com.br", has_rent=False),
    _widesys("morosini", "Flávio Morosini Corretor de Imóveis", "https://morosiniimoveis.com.br"),
    # --- Imonov / Si9 ---
    AgencyConfig(
        slug="america",
        name="Imobiliária América",
        base_url="https://www.imobiliariaamericapb.com.br",
        platform=PlatformKind.IMONOV,
        entry_urls=(
            (
                _BOTH,
                "https://www.imobiliariaamericapb.com.br/filtro/list/todos/todos/todas/todos/0-999999999/todos/1",
            ),
        ),
    ),
    AgencyConfig(
        slug="acacio",
        name="Acacio Imóveis",
        base_url="https://www.acacioimoveispb.com.br",
        platform=PlatformKind.IMONOV,
        entry_urls=(
            (
                _BOTH,
                "https://www.acacioimoveispb.com.br/filtro/todos/todos/todas/todos----todos/todos----todos/todos----todos/0-999999999/desc/1",
            ),
        ),
    ),
    AgencyConfig(
        slug="imperador",
        name="Imperador Imóveis",
        base_url="https://www.imperadorimoveispb.com.br",
        platform=PlatformKind.IMONOV,
        entry_urls=(
            (
                _BOTH,
                "https://www.imperadorimoveispb.com.br/filtro/list/todos/todos/todas/todos/0-999999999/todos/1",
            ),
        ),
    ),
    AgencyConfig(
        slug="mazorana",
        name="Mazorana Imóveis",
        base_url="https://www.mazoranaimoveis.imb.br",
        platform=PlatformKind.IMONOV,
        entry_urls=(
            (
                _BOTH,
                "https://www.mazoranaimoveis.imb.br/filtro/list/todos/todos/todas/todos/0-999999999/todos/1",
            ),
        ),
    ),
    AgencyConfig(
        slug="pauloreis",
        name="Paulo Reis Corretor de Imóveis",
        base_url="https://www.paulojreis.com.br",
        platform=PlatformKind.IMONOV,
        entry_urls=(
            (
                _BOTH,
                "https://www.paulojreis.com.br/filtro/todos/todos----todos/todas----todos/todos/todos/todos----todos/0-1000000000/0-1000000/1?mobiliado=nao&ofertas=nao&permuta=nao&condominio=nao&pet=nao&lancamento=nao&ord=desc&alarme=nao&cercaeletrica=nao&interfone=nao&porteiro24=nao&murado=nao&portao=nao&portaoeletronico=nao&grade=nao&playground=nao&sauna=nao&piscina=nao&academia=nao&lareira=nao&areagourmet=nao&quadrapoliesportiva=nao&salaodefesta=nao&hidromassagem=nao",
            ),
        ),
    ),
    AgencyConfig(
        slug="assmann",
        name="Assmann Imóveis",
        base_url="https://www.assmannimoveis.com.br",
        platform=PlatformKind.IMONOV,
        entry_urls=(
            (
                _RENT,
                "https://www.assmannimoveis.com.br/filtro/list/locacao/todos/todas/todos/0-99999999/todos/1",
            ),
            (
                _SALE,
                "https://www.assmannimoveis.com.br/filtro/list/venda/todos/todas/todos/0-99999999/todos/1",
            ),
        ),
    ),
    AgencyConfig(
        slug="trento",
        name="Imobiliária Trento",
        base_url="https://www.imobiliariatrento.com.br",
        platform=PlatformKind.IMONOV,
        entry_urls=(
            (
                _RENT,
                "https://www.imobiliariatrento.com.br/filtro/locacao/todos/todas/todos----todos/todos----todos/todos----todos/0-99999999/desc/1",
            ),
            (
                _SALE,
                "https://www.imobiliariatrento.com.br/filtro/venda/todos/todas/todos----todos/todos----todos/todos----todos/0-99999999/desc/1",
            ),
        ),
    ),
    AgencyConfig(
        slug="invest",
        name="Invest Imóveis Pato Branco",
        base_url="https://www.investimoveispatobranco.com.br",
        platform=PlatformKind.IMONOV,
        entry_urls=(
            (
                _RENT,
                "https://www.investimoveispatobranco.com.br/filtro/list/locacao/todos/todas----todos/todos----todos----todos/0-99999999----0-99999999/todos/1/asc?&pets=nao&condominio=nao&churrasqueira=nao&arcondicionado=nao&varanda=nao&elevador=nao&piscina=nao&porteiro=nao",
            ),
            (
                _SALE,
                "https://www.investimoveispatobranco.com.br/filtro/list/venda/todos/todas----todos/todos----todos----todos/0-99999999----0-99999999/todos/1/asc?&pets=nao&condominio=nao&churrasqueira=nao&arcondicionado=nao&varanda=nao&elevador=nao&piscina=nao&porteiro=nao",
            ),
        ),
    ),
    AgencyConfig(
        slug="belamorada",
        name="Imobiliária Bela Morada",
        base_url="https://www.imobiliariabelamorada.com.br",
        platform=PlatformKind.IMONOV,
        entry_urls=(
            (
                _RENT,
                "https://www.imobiliariabelamorada.com.br/filtro/locacao-list/todos/todas/todos/todos/todos/todos/todos/1",
            ),
            (
                _SALE,
                "https://www.imobiliariabelamorada.com.br/filtro/venda-list/todos/todas/todos/todos/todos/todos/todos/1",
            ),
        ),
    ),
    AgencyConfig(
        slug="habitar",
        name="Habitar Imobiliária",
        base_url="https://www.habitar.imb.br",
        platform=PlatformKind.IMONOV,
        entry_urls=(
            (
                _BOTH,
                "https://www.habitar.imb.br/filtro/todos/todos/todas/todos/todos---todos/todos/todos---todos/1/ASC",
            ),
        ),
    ),
    AgencyConfig(
        slug="conceitopb",
        name="Conceito PB Imóveis",
        base_url="https://www.conceitopbimoveis.com.br",
        platform=PlatformKind.IMONOV,
        entry_urls=(
            (
                _RENT,
                "https://www.conceitopbimoveis.com.br/filtro/list/locacao/todos/todas/todos/0-99999999/todos/todos/1",
            ),
            (
                _SALE,
                "https://www.conceitopbimoveis.com.br/filtro/list/venda/todos/todas/todos/0-99999999/todos/todos/1",
            ),
        ),
    ),
    AgencyConfig(
        slug="sbaraini",
        name="Sbaraini Imóveis",
        base_url="https://www.sbarainiimoveis.com.br",
        platform=PlatformKind.IMONOV,
        entry_urls=(
            (
                _RENT,
                "https://www.sbarainiimoveis.com.br/filtro/list/locacao/todos/todas/todos/0-99999999/todos/1",
            ),
            (
                _SALE,
                "https://www.sbarainiimoveis.com.br/filtro/list/venda/todos/todas/todos/0-99999999/todos/1",
            ),
        ),
    ),
    AgencyConfig(
        slug="ciro",
        name="Ciro Imóveis PB",
        base_url="https://www.ciroimoveispb.com.br",
        platform=PlatformKind.IMONOV,
        entry_urls=(
            (
                _RENT,
                "https://www.ciroimoveispb.com.br/filtro/list/locacao/todos/todas/todos/0-99999999/todos/1",
            ),
            (
                _SALE,
                "https://www.ciroimoveispb.com.br/filtro/list/venda/todos/todas/todos/0-99999999/todos/1",
            ),
        ),
    ),
    AgencyConfig(
        slug="cagolchaves",
        name="Imobiliária Cagol & Chaves",
        base_url="https://www.imobiliariacagolchaves.com.br",
        platform=PlatformKind.IMONOV,
        entry_urls=(
            (
                _RENT,
                "https://www.imobiliariacagolchaves.com.br/filtro/list/locacao/todos/todas/todos/0-99999999/todos/1",
            ),
            (
                _SALE,
                "https://www.imobiliariacagolchaves.com.br/filtro/list/venda/todos/todas/todos/0-99999999/todos/1",
            ),
        ),
    ),
    AgencyConfig(
        slug="evidence",
        name="Evidence Imóveis",
        base_url="https://www.evidence.imb.br",
        platform=PlatformKind.IMONOV,
        entry_urls=(
            (_RENT, "https://www.evidence.imb.br/filtro/list/locacao/todos/todas/todos/0-99999999/todos/1"),
            (_SALE, "https://www.evidence.imb.br/filtro/list/venda/todos/todas/todos/0-99999999/todos/1"),
        ),
    ),
    AgencyConfig(
        slug="famma",
        name="Imobiliária Famma",
        base_url="https://www.imobiliariafamma.com.br",
        platform=PlatformKind.IMONOV,
        entry_urls=(
            (
                _RENT,
                "https://www.imobiliariafamma.com.br/filtro/locacao/todos/todas/0-99999999/todos/todos/1/1",
            ),
            (
                _SALE,
                "https://www.imobiliariafamma.com.br/filtro/venda/todos/todas/0-99999999/todos/todos/1/1",
            ),
        ),
    ),
    AgencyConfig(
        slug="popiel",
        name="Popiel Imóveis",
        base_url="https://www.popielimoveis.com.br",
        platform=PlatformKind.IMONOV,
        entry_urls=(
            (
                _RENT,
                "https://www.popielimoveis.com.br/filtro/list/locacao/todos/todas/todos/todos/0-99999999/todos/1",
            ),
            (
                _SALE,
                "https://www.popielimoveis.com.br/filtro/list/venda/todos/todas/todos/todos/0-99999999/todos/1",
            ),
        ),
    ),
    AgencyConfig(
        slug="cad",
        name="CAD Correspondente Caixa e Imobiliária",
        base_url="https://www.cadimobiliaria.com.br",
        platform=PlatformKind.IMONOV,
        entry_urls=((_SALE, "https://www.cadimobiliaria.com.br/venda.asp?p=1"),),
    ),
    # --- 3 Custom / Dedicated Platform Agencies ---
    AgencyConfig(
        slug="uniq",
        name="Imobiliária Uniq",
        base_url="https://www.imobiliariauniq.com.br",
        platform=PlatformKind.UNIQ,
        entry_urls=(
            (_RENT, "https://www.imobiliariauniq.com.br/imoveis/aluguel"),
            (_SALE, "https://www.imobiliariauniq.com.br/imoveis/venda"),
        ),
    ),
    AgencyConfig(
        slug="moretti",
        name="Moretti Imobiliária",
        base_url="https://www.imobiliariamoretti.imb.br",
        platform=PlatformKind.MORETTI,
        entry_urls=((_BOTH, "https://www.imobiliariamoretti.imb.br/pt-BR/wp-json/myhome/v1/listings"),),
    ),
    AgencyConfig(
        slug="apolar",
        name="Apolar Imóveis (Pato Branco)",
        base_url="https://www.apolar.com.br",
        platform=PlatformKind.APOLAR,
        entry_urls=(
            (_BOTH, "https://uiyek91vqe.execute-api.us-east-1.amazonaws.com/prod/properties/search/main"),
        ),
        default_city_assumed=False,
    ),
    # Public HTML catalogs with same-site property links and published pagination.
    AgencyConfig(
        slug="valen",
        name="Valen Imobiliária",
        base_url="https://valenimobiliaria.com.br",
        platform=PlatformKind.PUBLIC_HTML,
        entry_urls=((_SALE, "https://valenimobiliaria.com.br/pesquisa"),),
        catalog_form=(("cidade", "Pato Branco"),),
        default_city_assumed=True,
    ),
    AgencyConfig(
        slug="cristian",
        name="Cristian de Mello Imóveis",
        base_url="https://cristianimoveis.com.br",
        platform=PlatformKind.PUBLIC_HTML,
        entry_urls=((_BOTH, "https://cristianimoveis.com.br/"),),
        default_city_assumed=False,
    ),
    AgencyConfig(
        slug="familiar",
        name="Imobiliária Familiar",
        base_url="https://familiar.imb.br",
        platform=PlatformKind.PUBLIC_HTML,
        entry_urls=((_BOTH, "https://familiar.imb.br/busca/"),),
        default_city_assumed=False,
    ),
    AgencyConfig(
        slug="fortun",
        name="Fortun Imóveis",
        base_url="https://www.fortunimoveis.com.br",
        platform=PlatformKind.AGIL,
        entry_urls=(
            (_BOTH, "https://new-agil-public.agil.net/company/667875e9-10f8-4c07-b91b-1ed3c890039a/search"),
        ),
        default_city_assumed=False,
    ),
    AgencyConfig(
        slug="alvaro",
        name="Álvaro Corretor de Imóveis",
        base_url="https://www.alvaroimoveis.imb.br",
        platform=PlatformKind.PUBLIC_HTML,
        entry_urls=(
            (_RENT, "https://www.alvaroimoveis.imb.br/imoveis/2-locacao"),
            (_SALE, "https://www.alvaroimoveis.imb.br/imoveis/1-venda"),
        ),
        default_city_assumed=False,
    ),
    AgencyConfig(
        slug="carlos",
        name="Carlos Corretor Imóveis",
        base_url="https://carlosimoveispb.com.br",
        platform=PlatformKind.PUBLIC_HTML,
        entry_urls=(
            (_SALE, "https://carlosimoveispb.com.br/imovel/?finalidade=venda&cidade=pato-branco"),
            (_RENT, "https://carlosimoveispb.com.br/imovel/?finalidade=locacao&cidade=pato-branco"),
        ),
        default_city_assumed=False,
    ),
    AgencyConfig(
        slug="mariely",
        name="Mariely Cattoni Corretora de Imóveis",
        base_url="https://marielycattoniimoveis.com.br",
        platform=PlatformKind.PUBLIC_HTML,
        entry_urls=((_SALE, "https://marielycattoniimoveis.com.br/imoveis"),),
        catalog_form=(("valorPesquisa", "Pato Branco"), ("pagina", "1")),
        default_city_assumed=False,
    ),
    AgencyConfig(
        slug="junior",
        name="Junior Gonçalves Corretor de Imóveis",
        base_url="https://juniorgoncalvesimoveis.com.br",
        platform=PlatformKind.PUBLIC_HTML,
        entry_urls=((_SALE, "https://juniorgoncalvesimoveis.com.br/imoveis-data.js"),),
        default_city_assumed=False,
    ),
)

AGENCIES_BY_SLUG: dict[str, AgencyConfig] = {agency.slug: agency for agency in AGENCIES}


def get_agency_config(slug: str) -> AgencyConfig | None:
    """Look up a registered agency configuration by slug.

    Args:
        slug: Unique agency identifier.

    Returns:
        Matching `AgencyConfig` if registered, otherwise `None`.
    """
    return AGENCIES_BY_SLUG.get(slug)

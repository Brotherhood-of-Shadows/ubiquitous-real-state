"""Unit tests for domain validation, reconciliation, city filtering, and deduplication."""

from __future__ import annotations

from imob.domain.enums import FurnishedStatus, ListingPurpose, PlatformKind, PropertyType
from imob.domain.models import AgencyConfig, RawScrapedItem
from imob.domain.normalizer import (
    detect_furnished,
    is_in_target_city,
    normalize_neighborhood,
    normalize_scraped_item,
    reconcile_property_type,
    reconcile_purpose,
)


def _make_agency(*, default_city_assumed: bool = True) -> AgencyConfig:
    """Create a reusable test agency configuration."""
    return AgencyConfig(
        slug="famex",
        name="Famex Imóveis",
        base_url="https://www.fameximoveis.com.br",
        platform=PlatformKind.WIDESYS,
        default_city_assumed=default_city_assumed,
    )


def test_city_filter_accepts_pato_branco_and_rejects_neighboring_or_coastal_cities() -> None:
    """Verify strict Pato Branco filtering across explicit city fields and title/URL fallbacks."""
    agency = _make_agency()
    fixtures = (
        RawScrapedItem(
            agency_slug="famex",
            external_id="1",
            source_url="https://www.fameximoveis.com.br/1-apto-centro-pato-branco",
            title="Apartamento no Centro",
            raw_city="Pato Branco/PR",
        ),
        RawScrapedItem(
            agency_slug="famex",
            external_id="2",
            source_url="https://www.fameximoveis.com.br/2-chacara-em-vitorino",
            title="Chácara à venda em Vitorino",
            raw_city=None,
        ),
        RawScrapedItem(
            agency_slug="famex",
            external_id="3",
            source_url="https://www.fameximoveis.com.br/3-apto-balneario-camboriu",
            title="Apartamento frente mar em Balneário Camboriú",
            raw_city="Balneário Camboriú - SC",
        ),
        RawScrapedItem(
            agency_slug="famex",
            external_id="4",
            source_url="https://www.fameximoveis.com.br/4-lote-pato-bragado",
            title="Terreno em Pato Bragado",
            raw_city="Pato Bragado",
        ),
        RawScrapedItem(
            agency_slug="famex",
            external_id="5",
            source_url="https://www.fameximoveis.com.br/5-cota-gramado",
            title="Cota de Apartamento para venda, Centro, Gramado",
            raw_city=None,
        ),
        RawScrapedItem(
            agency_slug="famex",
            external_id="6",
            source_url="https://www.fameximoveis.com.br/6-cobertura-dois-vizinhos",
            title="Cobertura para venda, - Bairro das Torres, Dois Vizinhos",
            raw_city="Pato Branco/PR",
        ),
    )

    actual = tuple(is_in_target_city(item, agency, target_city="Pato Branco") for item in fixtures)
    expected = (True, False, False, False, False, False)

    assert actual == expected


def test_reconcile_purpose_fixes_misfiled_sale_in_rental_section() -> None:
    """Verify high-value 'à venda' items misfiled under rental tabs are reclassified as sales."""
    fixture = (
        "aluguel",
        "Studio à venda no Edifício Horizon - Fraron",
        "https://www.fameximoveis.com.br/99-studio-a-venda-fraron",
        None,
        345_000.0,
    )

    actual = reconcile_purpose(*fixture)
    expected = (ListingPurpose.SALE, 345_000.0, None)

    assert actual == expected


def test_reconcile_property_type_refines_generic_crm_categories() -> None:
    """Verify generic CRM types ('Casa', 'Apartamento') are refined to sobrado, kitnet, cobertura, etc."""
    fixtures = (
        ("Casa", "Excelente Sobrado com 3 quartos no La Salle", "https://ex.com/1-sobrado"),
        ("Apartamento", "Kitnet mobiliada próxima à UTFPR no Fraron", "https://ex.com/2-kitnet"),
        ("Apartamento", "Cobertura Duplex no Centro com piscina", "https://ex.com/3-cobertura"),
        ("Imóvel", "Aluga Sala Comercial térrea no Centro", "https://ex.com/4-sala-comercial"),
        ("Comercial", "Barracão amplo na Baixada Industrial", "https://ex.com/5-barracao"),
        ("garagem", "Vaga de Garagem no Residencial Flamboyant", "https://ex.com/6-garagem"),
        ("Salas Comerciais", "Vaga de Garagem Privativa a venda - Ed João Gava", "https://ex.com/7-vaga"),
    )

    actual = tuple(reconcile_property_type(rt, ttl, url) for rt, ttl, url in fixtures)
    expected = (
        (PropertyType.SOBRADO, True),
        (PropertyType.KITNET_STUDIO, True),
        (PropertyType.PENTHOUSE, True),
        (PropertyType.COMMERCIAL_ROOM, True),
        (PropertyType.WAREHOUSE, True),
        (PropertyType.OTHER, False),
        (PropertyType.OTHER, True),
    )

    assert actual == expected


def test_normalize_neighborhood_handles_aliases_and_avoids_barracao_industrial_false_positive() -> None:
    """Verify Pato Branco neighborhood aliases canonicalize cleanly without false positives."""
    fixtures = (
        ("Bairro Jd. Floresta - Pato Branco", "Casa ampla", "https://ex.com/1"),
        ("Dal Ross", "Sobrado novo", "https://ex.com/2"),
        (None, "Barracão industrial amplo para logística", "https://ex.com/3-barracao-industrial"),
        (None, "Apartamento 2 quartos na Baixada Industrial", "https://ex.com/4-baixada-industrial"),
    )

    actual = tuple(normalize_neighborhood(nb, ttl, url) for nb, ttl, url in fixtures)
    expected = ("Jardim Floresta", "Dall Ross", None, "Baixada Industrial")

    assert actual == expected


def test_detect_furnished_distinguishes_semi_unfurnished_and_furnished() -> None:
    """Verify furnishing status avoids misclassifying 'não mobiliado' or 'semimobiliado' as 'mobiliado'."""
    fixtures = (
        (None, "apartamento com moveis planejados na cozinha"),
        (None, "apartamento nao mobiliado no centro"),
        (None, "studio 100% mobiliado proximo utfpr"),
        ("Não", "casa residencial"),
    )

    actual = tuple(detect_furnished(hint, text) for hint, text in fixtures)
    expected = (
        FurnishedStatus.SEMI_FURNISHED,
        FurnishedStatus.UNFURNISHED,
        FurnishedStatus.FURNISHED,
        FurnishedStatus.UNFURNISHED,
    )

    assert actual == expected


def test_normalize_scraped_item_computes_monthly_total_price_per_m2_and_amenities() -> None:
    """Verify end-to-end normalization extracts inline condo/IPTU fees, R$/m², building, and flags."""
    agency = _make_agency()
    raw = RawScrapedItem(
        agency_slug="famex",
        external_id="501",
        ref_code="AP501",
        source_url="https://www.fameximoveis.com.br/501-apartamento-centro",
        raw_purpose="aluguel",
        raw_type="Apartamento",
        title="Apartamento 2 quartos no Edifício Iguaçu - Centro",
        description=(
            "Ótimo apartamento com sacada, churrasqueira e elevador. "
            "Aceita pet pequeno. Condomínio aprox. R$ 350,00 e IPTU R$ 90,00."
        ),
        raw_city="Pato Branco",
        raw_neighborhood="Centro",
        rent_price=2_000.0,
        area_private_m2=80.0,
        parking_spots=1,
        image_urls=("https://www.fameximoveis.com.br/fotos/1.jpg",),
    )

    normalized = normalize_scraped_item(raw, agency)
    assert normalized is not None

    actual = (
        normalized.purpose,
        normalized.property_type,
        normalized.neighborhood,
        normalized.building_name,
        normalized.condo_fee,
        normalized.iptu_fee,
        normalized.effective_monthly_total,
        normalized.price_per_m2,
        normalized.bedrooms,
        normalized.accepts_pets,
        normalized.has_elevator,
        normalized.has_barbecue,
        normalized.has_balcony,
        normalized.dedup_group_key,
    )
    expected = (
        ListingPurpose.RENT,
        PropertyType.APARTMENT,
        "Centro",
        "Edifício Iguaçu",
        350.0,
        90.0,
        2_440.0,
        25.0,
        2,
        True,
        True,
        True,
        True,
        None,
    )

    assert actual == expected


def test_normalize_scraped_item_rejects_placeholder_sale_and_rent_amounts() -> None:
    """Verify unrealistic sale (< R$ 15k) or rent (< R$ 140) placeholders become price_on_request."""
    agency = _make_agency()
    raw_sale = RawScrapedItem(
        agency_slug="famex",
        external_id="9001",
        source_url="https://www.fameximoveis.com.br/9001-terreno-fraron",
        raw_purpose="venda",
        raw_type="Terreno",
        title="Terreno comercial no Fraron R$ 120/m²",
        raw_city="Pato Branco",
        sale_price=120.0,
    )

    norm = normalize_scraped_item(raw_sale, agency)
    assert norm is not None
    assert (norm.sale_price, norm.price_on_request) == (None, True)


def test_negotiation_respects_negation_and_explicit_json_false() -> None:
    """Permuta and proposals are independent flags; false is not inferred as true."""
    from imob.domain.normalizer import detect_negotiation

    assert detect_negotiation(None, "Aceita permuta. Estuda proposta.", "exchange")
    assert detect_negotiation(None, "Aceita permuta. Estuda proposta.", "proposal")
    assert not detect_negotiation(None, "Não aceita permuta", "exchange")
    assert not detect_negotiation(False, "Aceita permuta", "exchange")
    assert not detect_negotiation(None, "Não estuda proposta", "proposal")
    assert not detect_negotiation(None, "Proposta enviada pelo site", "proposal")

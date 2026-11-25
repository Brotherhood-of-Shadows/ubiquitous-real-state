"""Enumerations representing normalized real-estate domain concepts."""

from __future__ import annotations

from enum import StrEnum


class PlatformKind(StrEnum):
    """Underlying white-label CMS or custom engine powering an agency website."""

    WIDESYS = "widesys"
    IMONOV = "imonov"
    MORETTI = "moretti"
    UNIQ = "uniq"
    APOLAR = "apolar"
    PUBLIC_HTML = "public_html"
    AGIL = "agil"


class ListingPurpose(StrEnum):
    """Commercial transaction purpose of a property listing."""

    RENT = "aluguel"
    SALE = "venda"
    BOTH = "venda_e_aluguel"


class PropertyType(StrEnum):
    """Canonical validated property category."""

    APARTMENT = "apartamento"
    KITNET_STUDIO = "kitnet_studio"
    PENTHOUSE = "cobertura"
    HOUSE = "casa"
    SOBRADO = "sobrado"
    CONDO_HOUSE = "casa_condominio"
    COMMERCIAL_ROOM = "sala_comercial"
    COMMERCIAL_POINT = "ponto_loja"
    WAREHOUSE = "barracao"
    BUILDING = "predio"
    LAND = "terreno"
    RURAL = "chacara_rural"
    OTHER = "outro"


class FurnishedStatus(StrEnum):
    """Furnishing state detected from attributes or listing text."""

    FURNISHED = "mobiliado"
    SEMI_FURNISHED = "semimobiliado"
    UNFURNISHED = "nao_mobiliado"
    UNKNOWN = "nao_informado"


class SortOrder(StrEnum):
    """Supported sort strategies for property search queries."""

    PRICE_ASC = "menor_preco"
    PRICE_DESC = "maior_preco"
    MONTHLY_TOTAL_ASC = "menor_custo_mensal"
    PRICE_PER_M2_ASC = "menor_preco_m2"
    AREA_DESC = "maior_area"
    RECENT = "mais_recentes"
    PRICE_DROP = "maior_desconto"


class SyncStatus(StrEnum):
    """Execution outcome of an agency synchronization run."""

    SUCCESS = "success"
    PARTIAL = "partial"
    SKIPPED_TTL = "skipped_ttl"
    FAILED = "failed"

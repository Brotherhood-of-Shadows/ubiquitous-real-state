"""Scraper adapter for Apolar Imóveis REST API filtered strictly to Pato Branco."""

from __future__ import annotations

from typing import Any

from imob.domain.enums import ListingPurpose
from imob.domain.models import AgencyConfig, PropertyDetailPatch, RawScrapedItem
from imob.ports import ScrapeBatchResult
from imob.scrapers.http_client import PoliteHttpClient
from imob.scrapers.parsing import (
    is_valid_property_photo,
    parse_area_m2,
    parse_brl,
    parse_generic_detail_html,
)


def parse_apolar_item(raw: dict[str, Any], *, agency_slug: str = "apolar") -> RawScrapedItem | None:
    """Convert one Apolar search API JSON record into a `RawScrapedItem`.

    Args:
        raw: JSON object returned in `data[]` by Apolar's `properties/search/main`.
        agency_slug: Target agency slug (`'apolar'`).

    Returns:
        `RawScrapedItem` or `None` if missing essential identifiers.
    """
    id_imovel = raw.get("idImovel")
    if id_imovel is None:
        return None
    ext_id = str(id_imovel).strip()
    ref_code = str(raw.get("referencia_search") or raw.get("referencia") or "").strip() or None

    transacao = str(raw.get("transacao") or "").strip().lower()
    is_rent = bool(raw.get("IsLocacao")) or "loca" in transacao or "alug" in transacao
    raw_purpose = ListingPurpose.RENT.value if is_rent else ListingPurpose.SALE.value

    valor_venda = parse_brl(raw.get("valorvenda"))
    valor_locacao = parse_brl(raw.get("valorlocacao"))
    valor_consid = parse_brl(raw.get("valor_considerado"))

    if is_rent:
        rent_price = valor_locacao or valor_consid
        sale_price = valor_venda
    else:
        sale_price = valor_venda or valor_consid
        rent_price = valor_locacao

    prev_price = parse_brl(raw.get("ValorAnterior"))
    condo_fee = parse_brl(raw.get("vlrcondominio"))
    iptu_fee = parse_brl(raw.get("iptu"))
    insurance_fee = parse_brl(raw.get("seguro"))

    raw_type = str(raw.get("tipo") or raw.get("tipo_search") or "").strip()
    bairro = str(raw.get("bairro") or raw.get("bairro_search") or "").strip() or None
    cidade = str(raw.get("cidade") or raw.get("cidade_search") or "").strip() or None
    estado = str(raw.get("estado") or "PR").strip()

    street = str(raw.get("endereco") or "").strip() or None
    num_raw = str(raw.get("numero") or "").strip()
    street_num = num_raw if num_raw and num_raw != "0" else None
    cep_raw = raw.get("cep")
    postal_code = str(cep_raw).strip() if cep_raw else None
    building = str(raw.get("condominio") or "").strip() or None

    explicit_title = raw.get("Titulo")
    if isinstance(explicit_title, str) and explicit_title.strip():
        title = explicit_title.strip()
    else:
        verb = "para Locação" if is_rent else "à Venda"
        nb_part = f" no {bairro}" if bairro else ""
        title = f"{raw_type or 'Imóvel'} {verb}{nb_part} - Pato Branco"

    linksite = str(raw.get("linksite") or "").strip()
    if not linksite:
        linksite = f"https://www.apolar.com.br/?ref={ref_code or ext_id}"

    # Coordinates (Apolar names latitude EixoX and longitude EixoY)
    lat = _safe_float(raw.get("EixoX"))
    lng = _safe_float(raw.get("EixoY"))

    # Photos: foto_principal + popup_fotos.Foto[].URLArquivo[]
    photos: list[str] = []
    main_photo = raw.get("foto_principal")
    if isinstance(main_photo, str) and is_valid_property_photo(main_photo):
        photos.append(main_photo.strip())
    popup = raw.get("popup_fotos")
    if isinstance(popup, dict):
        foto_list = popup.get("Foto")
        if isinstance(foto_list, list):
            for entry in foto_list:
                if isinstance(entry, dict):
                    urls = entry.get("URLArquivo")
                    if isinstance(urls, list):
                        for u in urls:
                            if isinstance(u, str) and is_valid_property_photo(u):
                                photos.append(u.strip())

    beds = _safe_int(raw.get("dormitorios"))
    if beds is None:
        beds = _safe_int(raw.get("Quartos"))

    return RawScrapedItem(
        agency_slug=agency_slug,
        external_id=ext_id,
        source_url=linksite,
        raw_purpose=raw_purpose,
        raw_type=raw_type,
        ref_code=ref_code,
        title=title,
        description=str(raw.get("descricao") or "").strip() or None,
        raw_city=cidade,
        raw_state=estado,
        raw_neighborhood=bairro,
        street=street,
        street_number=street_num,
        postal_code=postal_code,
        building_name=building,
        sale_price=sale_price,
        rent_price=rent_price,
        previous_price=prev_price,
        condo_fee=condo_fee,
        iptu_fee=iptu_fee,
        insurance_fee=insurance_fee,
        area_private_m2=parse_area_m2(raw.get("area_util")),
        area_built_m2=parse_area_m2(raw.get("metragem")),
        area_total_m2=parse_area_m2(raw.get("areaterreno") or raw.get("area_total")),
        bedrooms=beds,
        suites=_safe_int(raw.get("suites")),
        bathrooms=_safe_int(raw.get("banheiro")),
        parking_spots=_safe_int(raw.get("garagem")),
        furnished_hint=str(raw.get("MobiliadoCombo") or "") or None,
        accepts_pets=bool(raw.get("AceitaPets") or raw.get("EspacoPet")),
        has_elevator=bool(raw.get("elevador") or raw.get("Elevador2")),
        has_pool=bool(raw.get("Piscina") or raw.get("Piscina25")),
        has_barbecue=bool(
            raw.get("Churrasqueira")
            or raw.get("Churrasqueira35")
            or raw.get("SacadacomChurrasqueira")
            or raw.get("EspacoGourmet")
        ),
        has_balcony=bool(raw.get("Sacada") or raw.get("Varanda") or raw.get("VarandaGourmet")),
        has_yard=bool(raw.get("Quintal") or raw.get("Jardim") or raw.get("garden")),
        accepts_exchange=_negotiation_flag(raw.get("permuta")),
        accepts_proposals=_negotiation_flag(raw.get("aceita_proposta")),
        latitude=lat,
        longitude=lng,
        image_urls=tuple(dict.fromkeys(photos))[:35],
        detail_complete=True,
    )


def _negotiation_flag(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None or str(value).strip() == "":
        return None
    normalized = str(value).strip().lower()
    if normalized in {"s", "sim", "true", "1"}:
        return True
    if normalized in {"n", "nao", "não", "false", "0"}:
        return False
    return None


def _safe_int(val: Any) -> int | None:
    if isinstance(val, bool) or val is None:
        return None
    try:
        num = int(val)
        return num if num >= 0 else None
    except (TypeError, ValueError):
        return None


def _safe_float(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


class ApolarScraper:
    """Async catalog scraper querying Apolar's search API exclusively for Pato Branco."""

    def __init__(self, http_client: PoliteHttpClient, *, target_city: str = "Pato Branco") -> None:
        """Initialize scraper with shared HTTP client and city filter.

        Args:
            http_client: Shared `PoliteHttpClient` instance.
            target_city: Target city queried directly in Apolar's search API.
        """
        self._http = http_client
        self._target_city = target_city

    async def fetch_catalog(self, agency: AgencyConfig, *, max_pages: int) -> ScrapeBatchResult:
        """Query `POST .../properties/search/main` in bulk slices for Pato Branco properties.

        Args:
            agency: Target Apolar agency configuration.
            max_pages: Safety limit on bulk JSON slices.

        Returns:
            `ScrapeBatchResult` with collected Pato Branco items and request count.
        """
        _ = max_pages
        endpoint = agency.entry_urls[0][1]
        collected: list[RawScrapedItem] = []
        seen_ids: set[str] = set()
        requests_made = 0
        bulk_size = 500

        queries: list[dict[str, Any]] = [
            {"city": self._target_city, "business": "Locacao", "size": bulk_size},
            {"city": self._target_city, "business": "Vendas", "size": bulk_size},
        ]

        idx = 0
        while idx < len(queries):
            body = queries[idx]
            idx += 1
            payload = await self._http.post_json(
                endpoint,
                agency_slug=agency.slug,
                json_body=body,
            )
            requests_made += 1
            rows = payload.get("data")
            if not isinstance(rows, list):
                continue

            for raw_obj in rows:
                if not isinstance(raw_obj, dict):
                    continue
                parsed = parse_apolar_item(raw_obj, agency_slug=agency.slug)
                if parsed is not None and parsed.external_id not in seen_ids:
                    seen_ids.add(parsed.external_id)
                    collected.append(parsed)

            total_reported = int(payload.get("total") or 0)
            if body.get("business") == "Vendas" and "order" not in body and total_reported > bulk_size:
                queries.append(
                    {
                        "city": self._target_city,
                        "business": "Vendas",
                        "size": bulk_size,
                        "order": "menor-valor",
                    }
                )

        return ScrapeBatchResult(items=tuple(collected), requests_made=requests_made)

    async def fetch_property_detail(self, agency: AgencyConfig, source_url: str) -> PropertyDetailPatch:
        """Return supplemental patch if requested (Apolar catalog already includes full details).

        Args:
            agency: Target agency configuration.
            source_url: Property URL.

        Returns:
            `PropertyDetailPatch` extracted from `source_url`.
        """
        status, _, html = await self._http.get_text(source_url, agency_slug=agency.slug)
        if status >= 400:
            return PropertyDetailPatch()
        return parse_generic_detail_html(html, agency.base_url)

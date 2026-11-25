"""Validation, reconciliation, and enrichment of untrusted scraped property data."""

from __future__ import annotations

import hashlib
import re
import unicodedata

from imob.domain.enums import FurnishedStatus, ListingPurpose, PropertyType
from imob.domain.models import AgencyConfig, NormalizedListing, RawScrapedItem

# Non-target cities that local Pato Branco agencies occasionally list
_OTHER_CITIES_NORM: tuple[str, ...] = (
    "vitorino",
    "mariopolis",
    "bom sucesso do sul",
    "coronel vivida",
    "clevelandia",
    "palmas",
    "chopinzinho",
    "francisco beltrao",
    "beltrao",
    "sao lourenco do oeste",
    "sao lourenco",
    "itapejara",
    "honorio serpa",
    "mangueirinha",
    "renascenca",
    "marmeleiro",
    "capanema",
    "realeza",
    "dois vizinhos",
    "sao jorge d oeste",
    "pato bragado",
    "curitiba",
    "sao jose dos pinhais",
    "cascavel",
    "foz do iguacu",
    "maringa",
    "londrina",
    "ponta grossa",
    "guarapuava",
    "balneario camboriu",
    "camboriu",
    "itapema",
    "meia praia",
    "porto belo",
    "bombinhas",
    "piçarras",
    "picarras",
    "itajai",
    "florianopolis",
    "joinville",
    "barra velha",
    "itapoa",
    "guaratuba",
    "matinhos",
    "caioba",
    "chapeco",
    "passo fundo",
    "gramado",
    "canela",
    "candoi",
    "irati",
    "nova prata do iguacu",
    "nova laranjeiras",
    "laranjeiras do sul",
    "ampere",
    "salto do lontra",
    "santa izabel do oeste",
)

# Canonical Pato Branco neighborhoods: normalized alias -> canonical display name
_PB_NEIGHBORHOODS: dict[str, str] = {
    "centro": "Centro",
    "fraron": "Fraron",
    "la salle": "La Salle",
    "lasalle": "La Salle",
    "brasilia": "Brasília",
    "santa terezinha": "Santa Terezinha",
    "vila isabel": "Vila Isabel",
    "sao cristovao": "São Cristóvão",
    "menino deus": "Menino Deus",
    "baixada industrial": "Baixada Industrial",
    "amadori": "Amadori",
    "parque do som": "Parque do Som",
    "bancarios": "Bancários",
    "cadorin": "Cadorin",
    "aeroporto": "Aeroporto",
    "planalto": "Planalto",
    "pinheirinho": "Pinheirinho",
    "sao vicente": "São Vicente",
    "jardim floresta": "Jardim Floresta",
    "jd floresta": "Jardim Floresta",
    "jardim primavera": "Jardim Primavera",
    "jd primavera": "Jardim Primavera",
    "jardim das americas": "Jardim das Américas",
    "jd das americas": "Jardim das Américas",
    "cristo rei": "Cristo Rei",
    "bela vista": "Bela Vista",
    "alvorada": "Alvorada",
    "novo horizonte": "Novo Horizonte",
    "morumbi": "Morumbi",
    "parzianello": "Parzianello",
    "sudoeste": "Sudoeste",
    "veneza": "Veneza",
    "industrial": "Industrial",
    "industrial ii": "Industrial",
    "gralha azul": "Gralha Azul",
    "sao francisco": "São Francisco",
    "sao roque": "São Roque",
    "bonatto": "Bonatto",
    "dall ross": "Dall Ross",
    "dal ross": "Dall Ross",
    "bortot": "Bortot",
    "trevo da guarani": "Trevo da Guarani",
    "anchieta": "Anchieta",
    "vila esperanca": "Vila Esperança",
    "santo antonio": "Santo Antônio",
    "sambugaro": "Sambugaro",
    "pagnoncelli": "Pagnoncelli",
    "sao joao": "São João",
    "vila verde": "Vila Verde",
    "passo da pedra": "Passo da Pedra",
    "alto da gloria": "Alto da Glória",
    "sao luiz": "São Luiz",
    "paulafonso": "Paulo Afonso",
    "paulo afonso": "Paulo Afonso",
    "area rural": "Área Rural",
    "zona rural": "Área Rural",
    "interior": "Área Rural",
}

# Sort longest aliases first so "baixada industrial" matches before "industrial"
_SORTED_NEIGHBORHOOD_ALIASES: tuple[tuple[str, str], ...] = tuple(
    sorted(_PB_NEIGHBORHOODS.items(), key=lambda kv: len(kv[0]), reverse=True)
)


def strip_accents(value: str) -> str:
    """Lowercase and strip diacritics from a text string.

    Args:
        value: Input string.

    Returns:
        ASCII-folded lowercase string with collapsed whitespace.
    """
    decomposed = unicodedata.normalize("NFKD", value)
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    plain = re.sub(r"[^a-z0-9\s/-]", " ", plain.lower())
    return re.sub(r"\s+", " ", plain).strip()


def slugify(value: str) -> str:
    """Convert text into a hyphenated lowercase ASCII slug.

    Args:
        value: Input text.

    Returns:
        Hyphen-separated slug string.
    """
    folded = strip_accents(value)
    return re.sub(r"[^a-z0-9]+", "-", folded).strip("-")


def sanitize_price(value: float | None) -> float | None:
    """Filter out placeholder prices such as R$ 0,00 or R$ 1,00.

    Args:
        value: Raw numeric price in BRL.

    Returns:
        Rounded positive BRL value, or `None` when invalid/placeholder.
    """
    if value is None:
        return None
    if value <= 50.0 or value >= 500_000_000.0:
        return None
    return round(float(value), 2)


def sanitize_fee(value: float | None) -> float | None:
    """Filter out invalid or zero monthly fees (condo, IPTU, insurance).

    Args:
        value: Raw fee amount in BRL.

    Returns:
        Rounded positive BRL fee, or `None` if zero/missing/unrealistic.
    """
    if value is None or value <= 1.0 or value >= 100_000.0:
        return None
    return round(float(value), 2)


def sanitize_area(value: float | None) -> float | None:
    """Validate square-meter measurements.

    Args:
        value: Area in m².

    Returns:
        Rounded area in m², or `None` if zero or implausible.
    """
    if value is None or value < 6.0 or value > 50_000_000.0:
        return None
    return round(float(value), 2)


def is_in_target_city(item: RawScrapedItem, agency: AgencyConfig, target_city: str = "Pato Branco") -> bool:
    """Determine whether a scraped listing belongs to the target city (Pato Branco).

    Args:
        item: Raw scraped item.
        agency: Source agency configuration.
        target_city: Desired city name.

    Returns:
        `True` if the listing belongs to `target_city`, `False` otherwise.
    """
    target_norm = strip_accents(target_city)
    title_norm = strip_accents(item.title)

    # Reject listings whose title ends with another city (even if CRM city defaulted to PB)
    if target_norm not in title_norm:
        for other in _OTHER_CITIES_NORM:
            if re.search(rf"(?:^|\b(?:em|no centro de)\s+|\s){re.escape(other)}$", title_norm):
                return False

    if item.raw_city:
        city_norm = strip_accents(item.raw_city)
        # Strip trailing state codes like "pato branco pr"
        city_norm = re.sub(r"\b(pr|parana|sc|rs)\b", "", city_norm).strip(" -/")
        if city_norm:
            return target_norm in city_norm

    # Exclude agency domain name when checking source_url so "investimoveispatobranco" doesn't force True
    url_path = re.sub(r"^https?://[^/]+", "", item.source_url)
    combined = strip_accents(f"{item.title} {url_path.replace('-', ' ')} {item.raw_neighborhood or ''}")
    for other in _OTHER_CITIES_NORM:
        if re.search(rf"\b{re.escape(other)}\b", combined) and not re.search(
            rf"\b(saida para|sentido|proximo a|trevo de)\s+{re.escape(other)}\b", combined
        ):
            return False

    if target_norm in combined:
        return True

    return agency.default_city_assumed


def reconcile_purpose(
    raw_purpose: str,
    title: str,
    source_url: str,
    sale_price: float | None,
    rent_price: float | None,
) -> tuple[ListingPurpose, float | None, float | None]:
    """Validate and reconcile listing purpose (aluguel vs venda) against text and prices.

    Args:
        raw_purpose: Purpose reported by the scraper section or CRM field.
        title: Property title.
        source_url: Canonical source URL.
        sale_price: Cleaned sale price if provided.
        rent_price: Cleaned rent price if provided.

    Returns:
        Tuple of `(ListingPurpose, reconciled_sale_price, reconciled_rent_price)`.
    """
    rp = strip_accents(raw_purpose)
    txt = strip_accents(f"{title} {source_url.replace('-', ' ')}")

    if sale_price is not None and rent_price is not None:
        return ListingPurpose.BOTH, sale_price, rent_price

    if "venda" in rp and ("locac" in rp or "alug" in rp):
        return ListingPurpose.BOTH, sale_price, rent_price

    mentions_sale = bool(re.search(r"\b(a venda|para venda|para compra|vende se|venda)\b", txt))
    mentions_rent = bool(re.search(r"\b(locacao|aluguel|aluga se|alugo|para alugar)\b", txt))

    is_rent_raw = any(tok in rp for tok in ("alug", "locac", "rent"))
    is_sale_raw = any(tok in rp for tok in ("vend", "compr", "sale", "lancamento"))

    # Catch agency misfiling: listed in rental tab with a sale title and high sale-like price
    if is_rent_raw and not is_sale_raw:
        if mentions_sale and not mentions_rent and rent_price is not None and rent_price >= 120_000.0:
            return ListingPurpose.SALE, rent_price, None
        return ListingPurpose.RENT, None, rent_price if rent_price is not None else sale_price

    if is_sale_raw and not is_rent_raw:
        if mentions_rent and not mentions_sale and sale_price is not None and sale_price <= 25_000.0:
            return ListingPurpose.RENT, None, sale_price
        return ListingPurpose.SALE, sale_price if sale_price is not None else rent_price, None

    # Fallback inference when raw_purpose was ambiguous ("ambos" / "todos")
    if mentions_rent and mentions_sale:
        return ListingPurpose.BOTH, sale_price, rent_price
    if mentions_rent or (rent_price is not None and sale_price is None):
        return ListingPurpose.RENT, None, rent_price if rent_price is not None else sale_price
    return ListingPurpose.SALE, sale_price if sale_price is not None else rent_price, None


def _classify_raw_type_string(raw: str) -> PropertyType:
    norm = strip_accents(raw)
    if not norm:
        return PropertyType.OTHER
    if re.search(r"\b(garagem|vaga de garagem|box de garagem)\b", norm):
        return PropertyType.OTHER
    if any(
        k in norm for k in ("kitnet", "kitinete", "quitinete", "studio", "estudio", "loft", "flat", "jk")
    ):
        return PropertyType.KITNET_STUDIO
    if "cobertura" in norm:
        return PropertyType.PENTHOUSE
    if "sobrado" in norm:
        return PropertyType.SOBRADO
    if re.search(r"\bcasa\b", norm) and "condominio" in norm:
        return PropertyType.CONDO_HOUSE
    if any(k in norm for k in ("sala", "conjunto", "consultorio", "escritorio")):
        return PropertyType.COMMERCIAL_ROOM
    if any(k in norm for k in ("ponto", "loja", "sobreloja", "comercial")):
        return PropertyType.COMMERCIAL_POINT
    if any(k in norm for k in ("barracao", "galpao", "pavilhao", "armazem", "deposito")):
        return PropertyType.WAREHOUSE
    if any(k in norm for k in ("predio", "edificio", "hotel", "corporativo")):
        return PropertyType.BUILDING
    if any(k in norm for k in ("chacara", "sitio", "fazenda", "rural")):
        return PropertyType.RURAL
    if any(k in norm for k in ("terreno", "lote", "area")):
        return PropertyType.LAND
    if "apartamento" in norm or norm == "apto":
        return PropertyType.APARTMENT
    if re.search(r"\b(casa|casas|residencia)\b", norm):
        return PropertyType.HOUSE
    return PropertyType.OTHER


def reconcile_property_type(raw_type: str, title: str, source_url: str) -> tuple[PropertyType, bool]:
    """Validate CRM property type and refine subtypes (sobrado, kitnet, sala, etc.) from title/URL.

    Args:
        raw_type: Raw category string provided by the agency.
        title: Listing headline.
        source_url: Listing URL.

    Returns:
        Tuple of `(PropertyType, was_reconciled)`.
    """
    raw_norm = strip_accents(raw_type)
    initial = _classify_raw_type_string(raw_type)
    url_path = re.sub(r"^https?://[^/]+", "", source_url)
    txt = strip_accents(f"{title} {url_path.replace('-', ' ')}")

    # Standalone parking spots / garage boxes stay OTHER rather than matching "Edifício"/"Residencial"
    if re.search(r"\b(garagem|box)\b", raw_norm) or re.search(
        r"^(locacao\s+|venda\s+|aluga\s+|alugo\s+)?(vagas? de garagem|box de garagem|garagem\b)",
        strip_accents(title),
    ):
        return PropertyType.OTHER, initial != PropertyType.OTHER

    # High-confidence text overrides when agencies use overly generic CRM buckets
    if re.search(r"\b(kitnet|kitinete|quitinete|studio|estudio|loft|flat)\b", txt) and initial in (
        PropertyType.APARTMENT,
        PropertyType.HOUSE,
        PropertyType.OTHER,
    ):
        return PropertyType.KITNET_STUDIO, True

    if re.search(r"\bcobertura\b", txt) and initial in (PropertyType.APARTMENT, PropertyType.OTHER):
        return PropertyType.PENTHOUSE, True

    if re.search(r"\bsobrado\b", txt) and initial in (PropertyType.HOUSE, PropertyType.OTHER):
        return PropertyType.SOBRADO, True

    if re.search(r"\bcasa\b.*\bcondominio\b", txt) and initial == PropertyType.HOUSE:
        return PropertyType.CONDO_HOUSE, True

    room_re = (
        r"^(locacao\s+|venda\s+|aluga\s+|alugo\s+)?"
        r"(sala comercial|sala terrea|sala no ed|conjunto comercial|consultorio)"
    )
    if re.search(room_re, txt) and initial != PropertyType.COMMERCIAL_ROOM:
        return PropertyType.COMMERCIAL_ROOM, True

    if re.search(r"\b(barracao|galpao|pavilhao)\b", txt) and initial in (
        PropertyType.OTHER,
        PropertyType.COMMERCIAL_POINT,
        PropertyType.HOUSE,
    ):
        return PropertyType.WAREHOUSE, True

    if re.search(r"\b(ponto comercial|loja comercial|loja terrea)\b", txt) and initial in (
        PropertyType.OTHER,
        PropertyType.COMMERCIAL_ROOM,
    ):
        return PropertyType.COMMERCIAL_POINT, True

    if re.search(r"\b(chacara|sitio|fazenda|area rural)\b", txt) and initial in (
        PropertyType.OTHER,
        PropertyType.LAND,
        PropertyType.HOUSE,
    ):
        return PropertyType.RURAL, True

    if initial == PropertyType.OTHER:
        inferred = _classify_raw_type_string(txt)
        if inferred != PropertyType.OTHER:
            return inferred, True

    return initial, False


def normalize_neighborhood(raw_neighborhood: str | None, title: str, source_url: str) -> str | None:
    """Resolve and canonicalize a Pato Branco neighborhood name.

    Args:
        raw_neighborhood: Raw neighborhood string from card/JSON if present.
        title: Listing title.
        source_url: Listing URL.

    Returns:
        Canonical display name (e.g., `'Centro'`, `'Jardim Floresta'`), or cleaned fallback.
    """
    if raw_neighborhood:
        cleaned = re.sub(r"(?i)^bairro\s+", "", raw_neighborhood).strip(" -/,.")
        norm = strip_accents(cleaned)
        norm = re.sub(r"\bpato branco.*$", "", norm).strip()
        if norm in _PB_NEIGHBORHOODS:
            return _PB_NEIGHBORHOODS[norm]
        for alias, canonical in _SORTED_NEIGHBORHOOD_ALIASES:
            if norm == alias or re.search(rf"\b{re.escape(alias)}\b", norm):
                return canonical
        if (
            cleaned
            and len(cleaned) <= 40
            and strip_accents(cleaned) not in ("pato branco", "pr", "todos", "todas")
        ):
            return cleaned.title()

    haystack = strip_accents(f"{title} {source_url.replace('-', ' ')}")
    for alias, canonical in _SORTED_NEIGHBORHOOD_ALIASES:
        # Avoid matching "industrial" when used as "barracao industrial" unless "bairro industrial"
        if alias == "industrial":
            if re.search(r"\bbairro industrial\b", haystack):
                return canonical
            continue
        if re.search(rf"\b{re.escape(alias)}\b", haystack):
            return canonical

    return None


def extract_building_name(explicit: str | None, title: str, description: str | None) -> str | None:
    """Extract building or residential condominium name when mentioned.

    Args:
        explicit: Explicit condo/building name from API if available.
        title: Property title.
        description: Optional property description.

    Returns:
        Cleaned building/condominium name or `None`.
    """
    if explicit and explicit.strip():
        return explicit.strip()[:80]
    source = f"{title} | {(description or '')[:300]}"
    m = re.search(
        r"\b((?:Edif[ií]cio|Ed\.|Residencial|Condom[ií]nio)\s+[A-ZÀ-Ú0-9][A-Za-zÀ-ÿ0-9\s'&-]{2,38}?)"
        r"(?=\s*[-–,|/()]|\s+(?:no|na|em|com|centro|bairro|pato|ref|para|a\s+venda)|\s*$)",
        source,
        re.IGNORECASE,
    )
    if m:
        candidate = re.sub(r"\s+", " ", m.group(1)).strip(" -–,.")
        if len(candidate) >= 6:
            return candidate[:80]
    return None


def detect_furnished(explicit_hint: str | None, text_blob: str) -> FurnishedStatus:
    """Detect furnishing level from explicit CRM hints or listing text.

    Args:
        explicit_hint: Furnished string from CRM if provided.
        text_blob: Lowercased ASCII-folded title + description.

    Returns:
        Resolved `FurnishedStatus`.
    """
    combined = strip_accents(f"{explicit_hint or ''} {text_blob}")
    if re.search(
        r"\b(semi\s*mobiliad[oa]|semimobiliad[oa]|moveis planejados|cozinha planejada)\b", combined
    ):
        return FurnishedStatus.SEMI_FURNISHED
    if re.search(r"\b(nao mobiliad[oa]|sem mobilia)\b", combined):
        return FurnishedStatus.UNFURNISHED
    if re.search(r"\bmobiliad[oa]\b", combined):
        return FurnishedStatus.FURNISHED
    if explicit_hint and strip_accents(explicit_hint) in ("nao", "no", "false", "0"):
        return FurnishedStatus.UNFURNISHED
    return FurnishedStatus.UNKNOWN


def _flag(explicit: bool | None, pattern: str, text_blob: str) -> bool:
    if explicit is True:
        return True
    if explicit is False:
        return bool(re.search(pattern, text_blob))
    return bool(re.search(pattern, text_blob))


def detect_negotiation(explicit: bool | None, text: str, kind: str) -> bool:
    """Detect explicit negotiation terms, respecting structured false and local negation."""
    if explicit is not None:
        return explicit
    term = r"(?:permuta|troca|carro|veiculo|terreno)" if kind == "exchange" else r"propostas?"
    for raw_sentence in re.split(r"[.!?;\n]", text):
        sentence = strip_accents(raw_sentence)
        if re.search(rf"\b(?:nao|sem)\b.{{0,35}}\b{term}\b", sentence):
            continue
        if re.search(
            rf"\b(?:aceita(?:m|mos)?|estuda(?:m|mos)?|avalia(?:m|mos)?)\s+(?:se\s+)?{term}\b", sentence
        ):
            return True
    return False


def normalize_scraped_item(
    item: RawScrapedItem,
    agency: AgencyConfig,
    *,
    target_city: str = "Pato Branco",
    target_state: str = "PR",
) -> NormalizedListing | None:
    """Validate, reconcile, and enrich a raw scraped listing into a domain record.

    Args:
        item: Untrusted raw scraped item.
        agency: Configuration of the source real-estate agency.
        target_city: City filter to enforce.
        target_state: State code for normalized records.

    Returns:
        Immutable `NormalizedListing` if valid and located in `target_city`, else `None`.
    """
    if not item.is_available or not item.external_id.strip() or not item.source_url.strip():
        return None

    if not is_in_target_city(item, agency, target_city=target_city):
        return None

    clean_sale = sanitize_price(item.sale_price)
    clean_rent = sanitize_price(item.rent_price)
    purpose, clean_sale, clean_rent = reconcile_purpose(
        item.raw_purpose,
        item.title,
        item.source_url,
        clean_sale,
        clean_rent,
    )
    # Reject unrealistic CRM placeholders (e.g., R$ 100 or R$/m² typed into total sale price)
    if clean_sale is not None and clean_sale < 15_000.0:
        clean_sale = None
    if clean_rent is not None and clean_rent < 140.0:
        clean_rent = None

    prop_type, reconciled = reconcile_property_type(item.raw_type, item.title, item.source_url)
    neighborhood = normalize_neighborhood(item.raw_neighborhood, item.title, item.source_url)
    neighborhood_slug = slugify(neighborhood) if neighborhood else None

    area_private = sanitize_area(item.area_private_m2)
    area_built = sanitize_area(item.area_built_m2)
    area_total = sanitize_area(item.area_total_m2)
    usable_area = (
        area_total
        if prop_type in (PropertyType.LAND, PropertyType.RURAL) and area_total is not None
        else (area_private or area_built or area_total)
    )

    condo_fee = sanitize_fee(item.condo_fee)
    iptu_fee = sanitize_fee(item.iptu_fee)
    insurance_fee = sanitize_fee(item.insurance_fee)

    # Extract condo / IPTU from description if missing
    desc_text = item.description or ""
    if condo_fee is None and desc_text:
        m_condo = re.search(
            r"(?i)condom[ií]nio\s*(?:aprox\.?|m[eé]dio|de|:)?\s*R\$\s*([\d.]+(?:,\d{2})?)", desc_text
        )
        if m_condo:
            condo_fee = sanitize_fee(_parse_brl_inline(m_condo.group(1)))
    if iptu_fee is None and desc_text:
        m_iptu = re.search(r"(?i)\biptu\s*(?:aprox\.?|mensal|de|:)?\s*R\$\s*([\d.]+(?:,\d{2})?)", desc_text)
        if m_iptu:
            iptu_fee = sanitize_fee(_parse_brl_inline(m_iptu.group(1)))

    effective_monthly: float | None = None
    if clean_rent is not None:
        effective_monthly = round(
            clean_rent + (condo_fee or 0.0) + (iptu_fee or 0.0) + (insurance_fee or 0.0),
            2,
        )

    reference_price = clean_sale if purpose == ListingPurpose.SALE else (clean_rent or clean_sale)
    price_per_m2: float | None = None
    if reference_price is not None and usable_area is not None and usable_area >= 10.0:
        price_per_m2 = round(reference_price / usable_area, 2)

    price_on_request = clean_sale is None and clean_rent is None

    clean_title = (
        re.sub(r"\s+", " ", item.title).strip()
        or f"{prop_type.value.replace('_', ' ').title()} em {neighborhood or target_city}"
    )
    text_blob = strip_accents(f"{clean_title} {desc_text}")

    # Infer bedrooms from title when card omitted it
    bedrooms = item.bedrooms if item.bedrooms is not None and 0 <= item.bedrooms <= 30 else None
    suites = item.suites if item.suites is not None and 0 <= item.suites <= 20 else None
    if bedrooms is None:
        m_q = re.search(r"\b(\d{1,2})\s*(?:quartos?|dormit[oó]rios?)\b", clean_title, re.I)
        if m_q:
            bedrooms = int(m_q.group(1))
    if suites is None:
        m_s = re.search(r"\b(\d{1,2})\s*su[ií]tes?\b", clean_title, re.I)
        if m_s:
            suites = int(m_s.group(1))
            if bedrooms is not None and bedrooms < suites:
                bedrooms = bedrooms + suites

    furnished = detect_furnished(item.furnished_hint, text_blob)
    accepts_pets = _flag(
        item.accepts_pets, r"\b(aceita pet|pet friendly|permite animais|espaco pet)\b", text_blob
    )
    has_elevator = _flag(item.has_elevator, r"\belevador\b", text_blob)
    has_pool = _flag(item.has_pool, r"\bpiscina\b", text_blob)
    has_barbecue = _flag(
        item.has_barbecue, r"\b(churrasqueira|espaco gourmet|varanda gourmet|area gourmet)\b", text_blob
    )
    has_balcony = _flag(item.has_balcony, r"\b(sacada|varanda|terraco)\b", text_blob)
    has_ac = _flag(item.has_air_conditioning, r"\b(ar condicionado|climatizad[oa])\b", text_blob)
    has_solar = _flag(item.has_solar, r"\b(energia solar|fotovoltaic[oa]|placas solares)\b", text_blob)
    has_yard = _flag(item.has_yard, r"\b(quintal|sobra de terreno|patio|ampl[oa] terreno)\b", text_blob)
    accepts_fin = _flag(
        item.accepts_financing,
        r"\b(financiavel|aceita financiamento|apto a financiamento|minha casa minha vida|mcmv)\b",
        text_blob,
    )
    accepts_exch = detect_negotiation(item.accepts_exchange, text_blob, "exchange")
    accepts_proposals = detect_negotiation(item.accepts_proposals, text_blob, "proposal")

    building = extract_building_name(item.building_name, clean_title, item.description)

    unique_imgs = tuple(
        dict.fromkeys(u.strip() for u in item.image_urls if u and u.strip().startswith("http"))
    )
    cover_img = unique_imgs[0] if unique_imgs else None

    fp_payload = (
        f"{purpose.value}|{prop_type.value}|{clean_sale}|{clean_rent}|{condo_fee}|{iptu_fee}|"
        f"{usable_area}|{bedrooms}|{suites}|{item.bathrooms}|{item.parking_spots}|{neighborhood_slug}|{clean_title}"
    )
    fp_hash = hashlib.sha256(fp_payload.encode("utf-8")).hexdigest()[:24]

    lat = item.latitude if item.latitude is not None and -35.0 <= item.latitude <= 5.0 else None
    lng = item.longitude if item.longitude is not None and -75.0 <= item.longitude <= -30.0 else None

    return NormalizedListing(
        agency_slug=agency.slug,
        external_id=item.external_id.strip(),
        ref_code=item.ref_code.strip() if item.ref_code and item.ref_code.strip() else None,
        source_url=item.source_url.strip(),
        whatsapp_url=item.whatsapp_url.strip() if item.whatsapp_url else None,
        purpose=purpose,
        property_type=prop_type,
        raw_type=item.raw_type.strip() or prop_type.value,
        raw_purpose=item.raw_purpose.strip() or purpose.value,
        type_reconciled=reconciled,
        title=clean_title[:280],
        description=item.description.strip() if item.description and item.description.strip() else None,
        city=target_city,
        state=target_state,
        neighborhood=neighborhood,
        neighborhood_slug=neighborhood_slug,
        street=item.street.strip()[:160] if item.street and item.street.strip() else None,
        street_number=item.street_number.strip()[:30]
        if item.street_number and item.street_number.strip()
        else None,
        postal_code=item.postal_code.strip()[:20]
        if item.postal_code and item.postal_code.strip()
        else None,
        building_name=building,
        sale_price=clean_sale,
        rent_price=clean_rent,
        condo_fee=condo_fee,
        iptu_fee=iptu_fee,
        insurance_fee=insurance_fee,
        effective_monthly_total=effective_monthly,
        price_per_m2=price_per_m2,
        price_on_request=price_on_request,
        previous_listed_price=sanitize_price(item.previous_price),
        area_private_m2=area_private,
        area_built_m2=area_built,
        area_total_m2=area_total,
        usable_area_m2=usable_area,
        bedrooms=bedrooms,
        suites=suites,
        bathrooms=item.bathrooms if item.bathrooms is not None and 0 <= item.bathrooms <= 20 else None,
        parking_spots=item.parking_spots
        if item.parking_spots is not None and 0 <= item.parking_spots <= 50
        else None,
        furnished_status=furnished,
        accepts_pets=accepts_pets,
        has_elevator=has_elevator,
        has_pool=has_pool,
        has_barbecue=has_barbecue,
        has_balcony=has_balcony,
        has_air_conditioning=has_ac,
        has_solar=has_solar,
        has_yard=has_yard,
        accepts_financing=accepts_fin,
        accepts_exchange=accepts_exch,
        accepts_proposals=accepts_proposals,
        latitude=lat,
        longitude=lng,
        cover_image_url=cover_img,
        image_urls=unique_imgs,
        dedup_group_key=None,
        fingerprint_hash=fp_hash,
        detail_complete=item.detail_complete,
    )


def _parse_brl_inline(raw: str) -> float | None:
    cleaned = raw.strip().replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None

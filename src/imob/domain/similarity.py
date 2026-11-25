"""Evidence-based candidate matching with explicit conflicts and non-transitive groups."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from functools import lru_cache
from itertools import combinations
from typing import Literal

from pydantic import BaseModel, ConfigDict

from imob.domain.normalizer import extract_building_name, strip_accents

_STOP_WORDS = frozenset(
    [
        "a",
        "o",
        "as",
        "os",
        "de",
        "do",
        "da",
        "dos",
        "das",
        "e",
        "em",
        "no",
        "na",
        "nos",
        "nas",
        "com",
        "para",
        "por",
        "ao",
        "um",
        "uma",
        "mais",
        "venda",
        "vender",
        "aluguel",
        "alugar",
        "locacao",
        "apartamento",
        "apartamentos",
        "casa",
        "casas",
        "terreno",
        "imovel",
        "chacara",
        "quartos",
        "quarto",
        "dormitorios",
        "dormitorio",
        "suite",
        "suites",
        "vagas",
        "garagem",
        "pato",
        "branco",
        "pr",
        "centro",
        "bairro",
        "residencial",
        "edificio",
        "ed",
        "condominio",
        "apto",
        "proximo",
    ]
)
_MULTI_UNIT = frozenset({"apartamento", "cobertura", "kitnet_studio", "sala_comercial", "casa_condominio"})


class SimilarityListing(BaseModel):
    """Immutable, persistence-independent input for approximate identity comparison."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    id: int
    agency_slug: str
    purpose: str
    property_type: str
    neighborhood: str | None = None
    street: str | None = None
    street_number: str | None = None
    building: str | None = None
    unit: str | None = None
    source_conflicts: tuple[str, ...] = ()
    title: str = ""
    description: str = ""
    area_private: float | None = None
    area_built: float | None = None
    area_total: float | None = None
    bedrooms: int | None = None
    suites: int | None = None
    bathrooms: int | None = None
    parking: int | None = None


@lru_cache(maxsize=32768)
def _tokens(text: str) -> frozenset[str]:
    text = re.sub(r"(?:R\s*\$|preço|preco|valor)\s*[:=]?\s*[\d.,]+", " ", text, flags=re.I)
    return frozenset(
        word
        for word in strip_accents(text).split()
        if word not in _STOP_WORDS and len(word) > 2 and not word.isdigit()
    )


@lru_cache(maxsize=32768)
def _street(value: str | None) -> str:
    return re.sub(r"^(?:rua|r|avenida|av)\s+", "", strip_accents(value or ""))


@lru_cache(maxsize=32768)
def _building(value: str | None) -> str:
    text = strip_accents(value or "")
    text = re.sub(r"^(?:(?:edificio|edifio|ed|residencial|condominio)\s+)+", "", text)
    text = re.split(
        r"\b(?:proximo|com|para|apartamento|apartamentos|apto|centro|rua|localizado|localizada|situado|situada)\b",
        text,
    )[0].strip()
    if text in {"fechado", "fechado de", "alto padrao", "novo", "lancamento", "em construcao"}:
        return ""
    return text if len(text) >= 4 and _tokens(text) else ""


@lru_cache(maxsize=32768)
def _unit(title: str) -> str:
    text = strip_accents(title)
    units = {
        match.group(1)
        for match in re.finditer(r"\b(?:ap|apto|apartamento|unidade|sala|casa)\s*(\d+[a-z]?)\b", text)
        if not re.match(r"\s*(?:quartos?|dormitorios?|suites?)\b", text[match.end() :])
    }
    return next(iter(units)) if len(units) == 1 else ""


@lru_cache(maxsize=32768)
def _title_address(title: str) -> tuple[str, str]:
    match = re.search(
        r"\b(?:rua|avenida|av|r)\s+([a-z][a-z ]{2,65}?)\s+(?:n(?:o|umero)?\s+)?(\d+[a-z]?)\b",
        strip_accents(title),
    )
    return (match.group(1).strip(), match.group(2)) if match else ("", "")


def _overlap(left: frozenset[str], right: frozenset[str]) -> float:
    return len(left & right) / len(left | right) if left and right else 0.0


class SimilarityAssessment(BaseModel):
    """Evidence classification, with a rule score rather than a probability."""

    model_config = ConfigDict(frozen=True)
    level: Literal["probable", "review", "none"] = "none"
    score: int = 0
    reasons: tuple[str, ...] = ()
    uncertainties: tuple[str, ...] = ()
    identical_features: tuple[str, ...] = ()


class SimilarityPair(BaseModel):
    """An undirected pair requiring probable grouping or manual review."""

    model_config = ConfigDict(frozen=True)
    left_id: int
    right_id: int
    assessment: SimilarityAssessment


@lru_cache(maxsize=16384)
def _property_description(description: str) -> str:
    """Read all property prose before an explicit broker contact/signature section."""
    return re.split(
        r"(?im)^\s*(?:agende\s+(?:uma\s+)?visita|para mais informa[çc][õo]es|"
        r"entre em contato|telefone\s*:|e-?mail\s*:|visite-nos|nosso escrit[óo]rio|"
        r"endere[çc]o da imobili[áa]ria|conhe[çc]a nossa imobili[áa]ria)",
        description,
        maxsplit=1,
    )[0]


@lru_cache(maxsize=16384)
def _text_building(title: str, description: str) -> str:
    for line in (title, *description.splitlines()):
        if value := extract_building_name(None, line, None):
            return _building(value)
    return ""


@lru_cache(maxsize=16384)
def prepare_listing(listing: SimilarityListing) -> SimilarityListing:
    """Supplement missing features from explicit wording, without changing stored source data."""
    description = _property_description(listing.description)
    text = "".join(
        ch
        for ch in unicodedata.normalize("NFKD", listing.title + " " + description)
        if not unicodedata.combining(ch)
    ).lower()
    title = strip_accents(listing.title)
    changes: dict[str, object] = {}
    conflicts = list(listing.source_conflicts)
    title_address = _title_address(listing.title)
    description_address = _title_address(description)
    for field, provided, inferred in (
        ("street", _street(listing.street) or title_address[0], description_address[0]),
        ("street_number", listing.street_number or title_address[1], description_address[1]),
    ):
        if provided and inferred and strip_accents(provided) != strip_accents(inferred):
            conflicts.append("Endereço estruturado/título e descrição divergem em " + field)
        elif not getattr(listing, field) and (provided or inferred):
            changes[field] = provided or inferred
    title_unit = listing.unit or _unit(listing.title)
    description_unit = _unit(description)
    if title_unit and description_unit and title_unit != description_unit:
        conflicts.append("Título e descrição identificam unidades diferentes")
    changes["unit"] = title_unit or description_unit or None
    changes["source_conflicts"] = tuple(dict.fromkeys(conflicts))
    words = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5}

    def count(pattern: str, source: str) -> int | None:
        match = re.search(rf"\b(\d{{1,2}}|um|uma|dois|duas|tres|quatro|cinco)\s+{pattern}\b", source)
        if not match:
            return None
        raw = match.group(1)
        value = int(raw) if raw.isdigit() else words[raw]
        return value if 0 <= value <= 30 else None

    for field, pattern in [
        ("bedrooms", r"(?:quartos?|dormitorios?)"),
        ("suites", r"suites?"),
        ("bathrooms", r"banheiros?"),
        ("parking", r"(?:vagas?|garagens?)"),
    ]:
        if getattr(listing, field) is not None:
            continue
        value = count(pattern, title)
        if value is None:
            value = count(pattern, strip_accents(description))
        if value is not None:
            changes[field] = value
    # Reconcile only explicitly disjoint rooms: "one suite + one bedroom".
    for room_source in (listing.title, description):
        normalized = strip_accents(room_source.replace("+", " mais "))
        beds = count(r"(?:quartos?|dormitorios?)", normalized)
        suites = count(r"suites?", normalized)
        disjoint = re.search(
            r"(?:quartos?|dormitorios?|suites?)(?:\s+com\s+[a-z ]{1,20})?\s+mais\s+"
            r"(?:\d+|um|uma|dois|duas|tres|quatro)\s+(?:quartos?|dormitorios?|suites?)",
            normalized,
        )
        if beds is not None and suites is not None and disjoint:
            changes["bedrooms"] = beds + suites
            break
    for field, pattern in [
        ("area_private", r"(?:area privativa|area util)"),
        ("area_built", r"area construida"),
        ("area_total", r"area total"),
    ]:
        if getattr(listing, field) is None:
            match = re.search(rf"{pattern}\s*[:=-]?\s*([\d.,]+)\s*m", text)
            if match:
                raw = match.group(1)
                try:
                    area_value = float(raw.replace(".", "").replace(",", ".")) if "," in raw else float(raw)
                except ValueError:
                    continue
                if 10 <= area_value <= 10000000:
                    changes[field] = area_value
    building = _building(listing.building) or _text_building(listing.title, description)
    neighborhood = strip_accents((listing.neighborhood or "").replace("-", " "))
    if neighborhood and building.endswith(" " + neighborhood):
        building = building[: -len(neighborhood)].strip()
    changes["building"] = building or None
    if listing.property_type in _MULTI_UNIT | {"casa", "sobrado"}:
        for field in ("bedrooms", "bathrooms"):
            if getattr(listing, field) == 0 and field not in changes:
                changes[field] = None
    return listing.model_copy(update=changes)


def assess_similarity(left: SimilarityListing, right: SimilarityListing) -> SimilarityAssessment:
    """Separate likely identity from reviewable similarity; never inspect property prices.

    Matching building and unit directly identify an apartment. Contradictory
    addresses/units veto candidates. Other count/area discrepancies force review
    only when identity depends on approximate characteristics.
    Missing fields contribute no evidence. Generic features alone cannot auto-group.
    """
    left, right = prepare_listing(left), prepare_listing(right)
    empty = SimilarityAssessment()
    if (left.purpose, left.property_type) != (right.purpose, right.property_type):
        return empty
    titles = (_title_address(left.title), _title_address(right.title))
    streets = (_street(left.street) or titles[0][0], _street(right.street) or titles[1][0])
    numbers = (
        strip_accents(left.street_number or "") or titles[0][1],
        strip_accents(right.street_number or "") or titles[1][1],
    )
    units = (left.unit or _unit(left.title), right.unit or _unit(right.title))
    buildings = (_building(left.building), _building(right.building))
    for a, b in (streets, numbers, units, buildings):
        if a and b and a != b:
            return empty
    address = bool(all(streets) and all(numbers))
    street = bool(all(streets))
    building = bool(all(buildings))
    unit = bool(all(units))
    if left.agency_slug == right.agency_slug and not (address or (building and unit)):
        return empty
    same_neighborhood = bool(left.neighborhood and left.neighborhood == right.neighborhood)
    identical_features: list[str] = []
    if street:
        identical_features.append("rua")
    elif building:
        identical_features.append("edifício/condomínio")
    if address:
        identical_features.append("número do endereço")
    if unit and (street or building):
        identical_features.append("unidade")
    physical = 0
    uncertainties: list[str] = list(dict.fromkeys((*left.source_conflicts, *right.source_conflicts)))
    for field, label in [
        ("bedrooms", "quartos"),
        ("suites", "suítes"),
        ("bathrooms", "banheiros"),
        ("parking", "vagas"),
    ]:
        a, b = getattr(left, field), getattr(right, field)
        if a is not None and b is not None:
            if a == b:
                physical += 1
                identical_features.append(label)
            else:
                uncertainties.append(f"Divergência em {label}: {a} / {b}")
    close_areas = 0
    identical_area = False
    exact_area = False
    for field, label in [
        ("area_private", "privativa"),
        ("area_built", "construída"),
        ("area_total", "total"),
    ]:
        a, b = getattr(left, field), getattr(right, field)
        if a is not None and b is not None and min(a, b) > 0:
            difference = abs(a - b) / max(a, b)
            identical_area |= abs(a - b) <= 0.000001
            if difference <= 0.03:
                close_areas += 1
                exact_area |= difference <= 0.005 and abs(a - round(a / 10) * 10) > 0.1
            elif difference > 0.08:
                uncertainties.append(f"Área {label} divergente: {a:g} / {b:g} m²")
    area = close_areas > 0
    if identical_area:
        identical_features.append("metragem do mesmo tipo")
    title_a, title_b = _tokens(left.title), _tokens(right.title)
    title_shared = len(title_a & title_b)
    title_sim = _overlap(title_a, title_b)
    title_support = title_shared >= 3 and title_sim >= 0.65
    desc_a, desc_b = _tokens(left.description), _tokens(right.description)
    description = len(desc_a & desc_b) >= 25 and _overlap(desc_a, desc_b) >= 0.8
    # Exact building + unit identifies the property without a third characteristic.
    # Otherwise location must anchor three independent exact matches. Street/building
    # describe one location feature; multiple area kinds still count as one size.
    direct_identity = building and unit
    strong = direct_identity or ((street or building) and len(identical_features) >= 3)
    review = (
        strong
        or (building and same_neighborhood and (physical >= 1 or area or unit))
        or (street and same_neighborhood and area and physical >= 1)
        or (title_support and same_neighborhood and (area or physical >= 2))
        or (exact_area and physical >= 3 and same_neighborhood)
    )
    if not review:
        return empty
    reasons: list[str] = []
    if direct_identity:
        reasons.append("Identificação direta: mesmo edifício e mesma unidade")
    if identical_features:
        reasons.append(
            f"{len(identical_features)} características idênticas: " + ", ".join(identical_features)
        )
    if address:
        reasons.append("Mesmo endereço e número")
    elif street:
        reasons.append("Mesma rua; número incompleto")
    if building:
        reasons.append("Mesmo edifício/condomínio")
    if unit:
        reasons.append("Mesma unidade identificada")
    elif left.property_type in _MULTI_UNIT:
        uncertainties.append("Unidade não confirmada; o edifício pode ter vários imóveis semelhantes")
    if area:
        reasons.append("Áreas do mesmo tipo próximas (até 3%)")
    else:
        uncertainties.append("Sem áreas comparáveis suficientes")
    if physical:
        reasons.append(f"{physical} atributos físicos compatíveis")
    if title_support:
        reasons.append("Título com termos específicos semelhantes")
    if description:
        reasons.append("Descrição detalhada fortemente semelhante")
    # Direct identity can survive inconsistent room/area metadata. Contradictory
    # source address/unit evidence still requires review; cross-listing conflicts
    # in building/address/unit have already been rejected above.
    divergence = any(
        "diverg" in warning.lower() or "diferentes" in warning.lower() for warning in uncertainties
    )
    identity_conflict = bool(left.source_conflicts or right.source_conflicts)
    automatic = strong and not identity_conflict and (direct_identity or not divergence)
    level: Literal["probable", "review"] = "probable" if automatic else "review"
    score = min(
        100,
        (40 if address else 15 if street else 0)
        + (30 if building else 0)
        + (20 if unit else 0)
        + (20 if area else 0)
        + physical * 4
        + (15 if title_support else 0)
        + (30 if description else 0),
    )
    if direct_identity and not identity_conflict:
        score = 100
    return SimilarityAssessment(
        level=level,
        score=score,
        reasons=tuple(reasons),
        uncertainties=tuple(uncertainties),
        identical_features=tuple(identical_features),
    )


def match_reasons(left: SimilarityListing, right: SimilarityListing) -> tuple[str, ...]:
    """Return reasons only for probable pairs eligible for automatic grouping."""
    assessment = assess_similarity(left, right)
    return assessment.reasons if assessment.level == "probable" else ()


def find_similarity_pairs(listings: tuple[SimilarityListing, ...]) -> tuple[SimilarityPair, ...]:
    """Build deterministic groups whose members all match each other, avoiding A-B-C chains.

    Inverted indexes bound candidate generation by address, building and specific
    text tokens. Common tokens (>80 listings) cannot generate candidate pairs.
    """
    listings = tuple(prepare_listing(listing) for listing in listings)
    by_id = {listing.id: listing for listing in listings}
    index: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for listing in listings:
        anchors = set()
        if street := _street(listing.street) or _title_address(listing.title)[0]:
            anchors.add("street:" + street)
        if building := _building(listing.building):
            anchors.add("building:" + building)
        for field in ("area_private", "area_built", "area_total"):
            if (value := getattr(listing, field)) is not None:
                for bucket in (int(value // 2), int(value // 2) + 1):
                    anchors.add(f"area:{listing.neighborhood}:{field}:{bucket}")
        anchors.update("word:" + word for word in _tokens(listing.title))
        anchors.update("desc:" + word for word in _tokens(listing.description))
        for anchor in anchors:
            index[(listing.purpose, listing.property_type, anchor)].append(listing.id)
    candidates: set[tuple[int, int]] = set()
    for (_, _, anchor), ids in index.items():
        if len(ids) <= 80 or not anchor.startswith(("word:", "desc:")):
            candidates.update(combinations(sorted(ids), 2))
    pairs: list[SimilarityPair] = []
    for a, b in sorted(candidates):
        assessment = assess_similarity(by_id[a], by_id[b])
        if assessment.level != "none":
            pairs.append(SimilarityPair(left_id=a, right_id=b, assessment=assessment))
    return tuple(pairs)


def group_similarity_pairs(pairs: tuple[SimilarityPair, ...]) -> dict[int, str]:
    """Group only probable pairs with complete linkage; review pairs never collapse results."""
    matches: dict[int, set[int]] = defaultdict(set)
    for pair in pairs:
        if pair.assessment.level == "probable":
            matches[pair.left_id].add(pair.right_id)
            matches[pair.right_id].add(pair.left_id)
    groups: list[list[int]] = []
    assigned: set[int] = set()
    for seed in sorted(matches):
        if seed in assigned:
            continue
        group = [seed]
        for candidate in sorted(matches[seed]):
            if candidate not in assigned and all(candidate in matches[member] for member in group):
                group.append(candidate)
        if len(group) > 1:
            groups.append(group)
            assigned.update(group)
    result: dict[int, str] = {}
    for group in groups:
        key = "similar-v7:" + hashlib.sha256(",".join(map(str, group)).encode()).hexdigest()[:32]
        result.update((member, key) for member in group)
    return result


def build_similarity_groups(listings: tuple[SimilarityListing, ...]) -> dict[int, str]:
    """Build probable groups while retaining review pairs outside automatic deduplication."""
    return group_similarity_pairs(find_similarity_pairs(listings))

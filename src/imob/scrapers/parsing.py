"""Pure parsing utilities for Brazilian currency, areas, counts, and detail HTML pages."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from imob.domain.models import PropertyDetailPatch
from imob.domain.normalizer import (
    detect_furnished,
    detect_negotiation,
    normalize_neighborhood,
    strip_accents,
)

_IGNORED_IMAGE_SUBSTRINGS: tuple[str, ...] = (
    "spinningwheel",
    "loading",
    "placeholder",
    "favicon",
    "webclip",
    "logo",
    "whatsapp",
    "ico-",
    "icon",
    "avatar",
    "watermark",
    "banner.png",
    "data:image",
    ".svg",
)


def has_empty_catalog_notice(html: str) -> bool:
    """Recognize an explicit empty catalog instead of mistaking unknown markup for success."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.select("script, style"):
        tag.decompose()
    text = strip_accents(soup.get_text(" ", strip=True))
    return bool(
        re.search(
            r"\b(?:nenhum imovel|nao ha imoveis|nao encontramos imoveis|"
            r"0\s+(?:imoveis|opcoes)|imoveis encontrados\s+0)\b",
            text,
        )
    )


def parse_brl(raw: str | float | int | None) -> float | None:
    """Parse a Brazilian Real currency string or number into a float.

    Args:
        raw: Currency text (e.g. `'R$ 2.450,00'`, `'340.000'`) or numeric value.

    Returns:
        Parsed float amount or `None` when absent/non-numeric.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw) if raw > 0 else None
    txt = raw.strip()
    if not txt:
        return None
    lower = txt.lower()
    if "consulte" in lower or "sob consulta" in lower:
        return None

    num_pat = r"(\d{1,3}(?:\.\d{3})+,\d{2}|\d+,\d{2}|\d{1,3}(?:\.\d{3})+|\d+(?:\.\d+)?)"
    m = re.search(rf"(?i)R\$\s*{num_pat}", txt) if "r$" in lower else re.search(num_pat, txt)
    if not m:
        return None
    token = m.group(1)
    if "," in token:
        cleaned = token.replace(".", "").replace(",", ".")
    elif token.count(".") >= 1 and len(token.split(".")[-1]) == 3:
        cleaned = token.replace(".", "")
    else:
        cleaned = token
    try:
        val = float(cleaned)
    except ValueError:
        return None
    return val if val > 0 else None


def parse_area_m2(raw: str | float | int | None) -> float | None:
    """Extract square-meter numeric measurement from text or numeric value.

    Args:
        raw: Area string (e.g. `'120,50 m²'`, `'1.070 m²'`, `'120.00m²'`).

    Returns:
        Area in m² or `None`.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw) if raw > 0 else None
    txt = raw.strip()
    if not txt:
        return None
    # Strip trailing m2 / m² so '2' isn't captured as part of number
    txt_clean = re.sub(r"(?i)m(?:²|2|\b).*$", "", txt).strip()
    m = re.search(r"(\d{1,3}(?:\.\d{3})+,\d+|\d+,\d+|\d{1,3}(?:\.\d{3})+|\d+(?:\.\d+)?)", txt_clean)
    if not m:
        return None
    token = m.group(1)
    if "," in token:
        cleaned = token.replace(".", "").replace(",", ".")
    elif token.count(".") >= 1 and len(token.split(".")[-1]) == 3:
        cleaned = token.replace(".", "")
    else:
        cleaned = token
    try:
        val = float(cleaned)
    except ValueError:
        return None
    return val if val > 0 else None


def parse_first_int(raw: str | int | None) -> int | None:
    """Extract the first non-negative integer from text.

    Args:
        raw: Input string or integer.

    Returns:
        Integer value or `None`.
    """
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    m = re.search(r"\b(\d{1,3})\b", raw)
    return int(m.group(1)) if m else None


def is_valid_property_photo(url: str) -> bool:
    """Filter out UI icons, logos, lazy-load spinners, and SVG assets from photo galleries.

    Args:
        url: Candidate image URL.

    Returns:
        `True` if the URL looks like an actual property photograph.
    """
    u = url.strip()
    if not u or not u.startswith(("http://", "https://")):
        return False
    lower = u.lower()
    return not any(bad in lower for bad in _IGNORED_IMAGE_SUBSTRINGS)


def extract_image_from_tag(img: Tag, base_url: str) -> str | None:
    """Extract the highest-resolution photo URL from an `<img>` element.

    Args:
        img: BeautifulSoup `<img>` tag.
        base_url: Base URL for resolving relative paths.

    Returns:
        Absolute photo URL or `None`.
    """
    for srcset_attr in ("data-srcset", "srcset"):
        srcset = img.get(srcset_attr)
        if isinstance(srcset, str) and srcset.strip():
            best_url: str | None = None
            best_w = -1
            for part in srcset.split(","):
                pieces = part.strip().split()
                if not pieces:
                    continue
                candidate = urljoin(base_url, pieces[0])
                if not is_valid_property_photo(candidate):
                    continue
                w = 0
                if len(pieces) > 1 and pieces[1].endswith("w") and pieces[1][:-1].isdigit():
                    w = int(pieces[1][:-1])
                if w >= best_w:
                    best_w = w
                    best_url = candidate
            if best_url:
                return best_url

    for attr in ("data-src", "data-lazy", "data-original", "src"):
        val = img.get(attr)
        if isinstance(val, str) and val.strip():
            resolved = urljoin(base_url, val.strip())
            if is_valid_property_photo(resolved):
                return resolved
    return None


def parse_generic_detail_html(html: str, base_url: str) -> PropertyDetailPatch:
    """Extract rich description, fees, dimensions, coordinates, and gallery from a property page.

    Args:
        html: Full HTML of an individual property detail page.
        base_url: Canonical URL of the property page.

    Returns:
        `PropertyDetailPatch` populated with all discovered supplemental attributes.
    """
    soup = BeautifulSoup(html, "lxml")
    for noisy in soup.select("script, style, nav, header, footer, noscript"):
        noisy.decompose()

    # Extract description from common real-estate CMS containers
    desc_candidates: list[str] = []
    for selector in (
        ".property-description",
        ".imovel-descricao",
        ".descricao-imovel",
        ".descricao",
        ".ResumoDescritivo",
        ".mh-estate__section--description",
        "[itemprop='description']",
        ".tab-pane",
    ):
        for el in soup.select(selector):
            t = el.get_text("\n", strip=True)
            if len(t) >= 40:
                desc_candidates.append(t)

    if not desc_candidates:
        # Look for paragraphs following a "Descrição" heading
        for heading in soup.find_all(["h2", "h3", "h4", "strong"]):
            if "descri" in strip_accents(heading.get_text(" ", strip=True)):
                parent = heading.parent
                if isinstance(parent, Tag):
                    t = parent.get_text("\n", strip=True)
                    if len(t) >= 40:
                        desc_candidates.append(t)

    description = max(desc_candidates, key=len)[:4000] if desc_candidates else None
    full_text = soup.get_text(" ", strip=True)

    condo_fee: float | None = None
    m_condo = re.search(
        r"(?i)condom[ií]nio\s*(?:aprox\.?|m[eé]dio|:|-)?\s*R\$\s*([\d.]+(?:,\d{2})?)", full_text
    )
    if m_condo:
        condo_fee = parse_brl(m_condo.group(1))

    iptu_fee: float | None = None
    m_iptu = re.search(r"(?i)\biptu\s*(?:aprox\.?|mensal|:|-)?\s*R\$\s*([\d.]+(?:,\d{2})?)", full_text)
    if m_iptu:
        iptu_fee = parse_brl(m_iptu.group(1))

    area_priv: float | None = None
    m_ap = re.search(r"(?i)[aá]rea\s+(?:privativa|[uú]til)\s*[:\-]?\s*([\d.,]+)\s*m", full_text)
    if m_ap:
        area_priv = parse_area_m2(m_ap.group(1))

    area_built: float | None = None
    m_ab = re.search(r"(?i)[aá]rea\s+constru[ií]da\s*[:\-]?\s*([\d.,]+)\s*m", full_text)
    if m_ab:
        area_built = parse_area_m2(m_ab.group(1))

    area_tot: float | None = None
    m_at = re.search(r"(?i)[aá]rea\s+(?:total|do\s+terreno|terreno)\s*[:\-]?\s*([\d.,]+)\s*m", full_text)
    if m_at:
        area_tot = parse_area_m2(m_at.group(1))

    # Extract coordinates from raw HTML (including scripts before decompose)
    lat: float | None = None
    lng: float | None = None
    m_coord = re.search(r"(-26\.\d{4,8})\s*,\s*(-52\.\d{4,8})", html)
    if m_coord:
        lat = float(m_coord.group(1))
        lng = float(m_coord.group(2))

    # Gallery photos from fresh soup (excluding related/similar property cards)
    raw_soup = BeautifulSoup(html, "lxml")
    for related in raw_soup.select(
        ".related, .similares, .imoveis-semelhantes, .outros-imoveis, footer, header"
    ):
        related.decompose()

    gallery: list[str] = []
    for a_tag in raw_soup.select("a[data-fancybox], a.fancybox, a.glightbox, a[data-lightbox]"):
        href = a_tag.get("href")
        if isinstance(href, str):
            u = urljoin(base_url, href)
            if is_valid_property_photo(u) and any(
                ext in u.lower()
                for ext in (".jpg", ".jpeg", ".png", ".webp", "widesys", "si9dados", "apresenta.me")
            ):
                gallery.append(u)

    for img in raw_soup.find_all("img"):
        img_u = extract_image_from_tag(img, base_url)
        if img_u and any(
            tok in img_u.lower()
            for tok in (
                "widesys",
                "si9dados",
                "apresenta.me",
                "wp-content/uploads",
                "apolar",
                "/fotos/",
                "/imoveis/",
            )
        ):
            gallery.append(img_u)

    norm_blob = strip_accents(f"{description or ''} {full_text[:3000]}")
    furnished = detect_furnished(None, norm_blob)
    nb = normalize_neighborhood(None, full_text[:400], base_url)

    return PropertyDetailPatch(
        description=description,
        condo_fee=condo_fee,
        iptu_fee=iptu_fee,
        area_private_m2=area_priv,
        area_built_m2=area_built,
        area_total_m2=area_tot,
        neighborhood=nb,
        latitude=lat,
        longitude=lng,
        furnished_status=furnished,
        accepts_pets=bool(re.search(r"\b(aceita pet|pet friendly|permite animais)\b", norm_blob)),
        has_elevator=bool(re.search(r"\belevador\b", norm_blob)),
        has_pool=bool(re.search(r"\bpiscina\b", norm_blob)),
        has_barbecue=bool(re.search(r"\b(churrasqueira|espaco gourmet|area gourmet)\b", norm_blob)),
        has_balcony=bool(re.search(r"\b(sacada|varanda)\b", norm_blob)),
        has_air_conditioning=bool(re.search(r"\b(ar condicionado|climatizad[oa])\b", norm_blob)),
        has_solar=bool(re.search(r"\b(energia solar|fotovoltaic[oa])\b", norm_blob)),
        has_yard=bool(re.search(r"\b(quintal|sobra de terreno)\b", norm_blob)),
        accepts_financing=bool(re.search(r"\b(financiavel|aceita financiamento)\b", norm_blob)),
        accepts_exchange=detect_negotiation(None, description or "", "exchange"),
        accepts_proposals=detect_negotiation(None, description or "", "proposal"),
        image_urls=tuple(dict.fromkeys(gallery))[:40],
    )

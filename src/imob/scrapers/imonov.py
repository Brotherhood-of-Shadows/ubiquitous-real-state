"""Scraper adapter for Imonov / Si9 Sistemas real-estate websites."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from imob.domain.enums import ListingPurpose
from imob.domain.models import AgencyConfig, PropertyDetailPatch, RawScrapedItem
from imob.errors import ScraperTransportError
from imob.ports import ScrapeBatchResult
from imob.scrapers.http_client import PoliteHttpClient
from imob.scrapers.parsing import (
    extract_image_from_tag,
    has_empty_catalog_notice,
    parse_area_m2,
    parse_brl,
    parse_first_int,
    parse_generic_detail_html,
)

# Canonical Imonov property URL: /imovel/{purpose}/{type}/{city-uf}/{bairro}/{slug}/{id}
_IMONOV_URL_RE = re.compile(
    r"/imovel/([a-z0-9-]+)/([a-z0-9-]+)/([a-z0-9-]+)/([a-z0-9-]+)/([^/?#]+)/(\d+)",
    re.IGNORECASE,
)

# Leaflet map marker pattern present on sites like Habitar
_LEAFLET_MARKER_RE = re.compile(
    r"L\.marker\(\[\s*(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)\s*\]\)[^\n]*?bindPopup\('(.*?)'\);",
    re.DOTALL,
)


def set_imonov_page_number(entry_url: str, page: int) -> str:
    """Substitute the page segment in any of the Imonov `/filtro/...` or `?p=1` URL patterns.

    Args:
        entry_url: Page-1 Imonov filter URL.
        page: 1-based page number to substitute.

    Returns:
        Paginated Imonov filter URL.
    """
    if page <= 1:
        return entry_url
    if re.search(r"[?&]p=1\b", entry_url):
        return re.sub(r"([?&]p=)1\b", rf"\g<1>{page}", entry_url, count=1)
    return re.sub(
        r"/1(?=(?:/(?:ASC|DESC|asc|desc))?(?:\?.*)?$)",
        f"/{page}",
        entry_url,
        count=1,
    )


def _city_state_from_slug(city_slug: str) -> tuple[str, str]:
    slug = city_slug.strip("-").lower()
    m = re.match(r"^(.+)-(pr|sc|rs|sp|mt|ms)$", slug)
    if m:
        return m.group(1).replace("-", " ").title(), m.group(2).upper()
    return slug.replace("-", " ").title(), "PR"


def parse_imonov_listing_html(
    html: str,
    *,
    agency_slug: str,
    base_url: str,
    default_purpose: ListingPurpose,
) -> list[RawScrapedItem]:
    """Parse an Imonov / Si9 listing page across all Webflow template variants.

    Args:
        html: Listing HTML returned by `/filtro/...`.
        agency_slug: Source agency slug.
        base_url: Canonical agency domain URL.
        default_purpose: Fallback section purpose.

    Returns:
        List of `RawScrapedItem` extracted from the page.
    """
    # 1. Extract GPS coordinates & popup fallbacks from inline Leaflet markers before stripping scripts
    leaflet_info: dict[str, tuple[float, float, str | None, float | None, str | None]] = {}
    for m_lat, m_lng, popup_html in _LEAFLET_MARKER_RE.findall(html):
        m_url = _IMONOV_URL_RE.search(popup_html)
        if not m_url:
            continue
        ext_id = m_url.group(6)
        pop_soup = BeautifulSoup(popup_html, "lxml")
        h6s = [h.get_text(" ", strip=True) for h in pop_soup.find_all(["h6", "h5"])]
        pop_title = h6s[0] if h6s else None
        m_pop_brl = re.search(r"R\$\s*([\d.]+(?:,\d{2})?)", " ".join(h6s))
        pop_price = parse_brl(m_pop_brl.group(0)) if m_pop_brl else None
        img_el = pop_soup.find("img")
        pop_img = extract_image_from_tag(img_el, base_url) if isinstance(img_el, Tag) else None
        leaflet_info[ext_id] = (float(m_lat), float(m_lng), pop_title, pop_price, pop_img)

    soup = BeautifulSoup(html, "lxml")
    for s_tag in soup.find_all("script"):
        s_tag.decompose()

    # 2. Index all property anchors outside scripts
    anchors_by_id: dict[str, list[tuple[Tag, re.Match[str]]]] = {}
    for a_tag in soup.find_all("a", href=True):
        href = str(a_tag["href"]).strip()
        m = _IMONOV_URL_RE.search(href)
        if not m:
            continue
        ext_id = m.group(6)
        anchors_by_id.setdefault(ext_id, []).append((a_tag, m))

    results: list[RawScrapedItem] = []
    seen_ids: set[str] = set()

    for ext_id, anchor_list in anchors_by_id.items():
        seen_ids.add(ext_id)
        primary_a, url_match = anchor_list[0]
        purpose_slug, type_slug, city_slug, bairro_slug, title_slug, _ = url_match.groups()

        # Normalize staging hostnames (e.g., hospedagemdesites.ws) to agency's canonical domain
        raw_href = str(primary_a["href"]).strip()
        parsed_href = urlparse(raw_href)
        canonical_url = urljoin(base_url, parsed_href.path)

        card = _find_exclusive_card_container(primary_a, ext_id)
        raw_city, raw_state = _city_state_from_slug(city_slug)
        raw_neighborhood = bairro_slug.replace("-", " ")

        # Title
        title = ""
        for sel in ("h3", "h4", ".heading-4", ".t-imoveis", ".text-block-3"):
            el = card.select_one(sel)
            if el:
                cand = el.get_text(" ", strip=True)
                if len(cand) >= 8 and not cand.lower().startswith("ref"):
                    title = cand
                    break
        if not title:
            # Bela Morada puts title in first .property-price div
            pp_els = card.select(".property-price")
            for pp in pp_els:
                cand = pp.get_text(" ", strip=True)
                if len(cand) >= 10 and not re.match(r"^[\d.,\sR$]+$", cand):
                    title = cand
                    break
        if not title:
            for img in card.find_all("img", alt=True):
                cand = str(img.get("alt") or "").strip()
                if len(cand) >= 10 and "imagem do im" not in cand.lower():
                    title = cand
                    break
        if not title:
            title = title_slug.replace("-", " ").strip().capitalize()

        card_text = card.get_text(" ", strip=True)

        # Reference code
        ref_code: str | None = None
        m_ref = re.search(r"(?i)\bRef\.?\s*:?\s*([A-Za-z0-9-]{1,20})\b", card_text)
        if m_ref:
            ref_code = m_ref.group(1)

        # Purpose & Price
        purpose_str = purpose_slug.replace("-", " ")
        if purpose_slug == "todos":
            purpose_str = default_purpose.value

        prices = _extract_imonov_prices(card, card_text)
        sale_price: float | None = None
        rent_price: float | None = None
        if "venda" in purpose_slug and "locacao" in purpose_slug:
            if len(prices) >= 2:
                sale_price = max(prices)
                rent_price = min(prices)
            elif len(prices) == 1:
                if prices[0] >= 80_000:
                    sale_price = prices[0]
                else:
                    rent_price = prices[0]
        elif "locacao" in purpose_slug or default_purpose == ListingPurpose.RENT:
            rent_price = prices[0] if prices else None
        else:
            sale_price = prices[0] if prices else None

        # Areas & room counts
        area_priv, area_tot, beds, suites, baths, parking = _extract_imonov_features(card, card_text)

        # Images
        photos: list[str] = []
        for img in card.find_all("img"):
            u = extract_image_from_tag(img, base_url)
            if u:
                photos.append(u)

        lat: float | None = None
        lng: float | None = None
        if ext_id in leaflet_info:
            lat, lng, pop_title, pop_price, pop_img = leaflet_info[ext_id]
            if not title and pop_title:
                title = pop_title
            if sale_price is None and rent_price is None and pop_price is not None:
                if "locacao" in purpose_slug:
                    rent_price = pop_price
                else:
                    sale_price = pop_price
            if pop_img and pop_img not in photos:
                photos.insert(0, pop_img)

        results.append(
            RawScrapedItem(
                agency_slug=agency_slug,
                external_id=ext_id,
                source_url=canonical_url,
                raw_purpose=purpose_str,
                raw_type=type_slug.replace("-", " "),
                ref_code=ref_code,
                title=title,
                raw_city=raw_city,
                raw_state=raw_state,
                raw_neighborhood=raw_neighborhood,
                sale_price=sale_price,
                rent_price=rent_price,
                area_private_m2=area_priv,
                area_total_m2=area_tot,
                bedrooms=beds,
                suites=suites,
                bathrooms=baths,
                parking_spots=parking,
                latitude=lat,
                longitude=lng,
                image_urls=tuple(dict.fromkeys(photos)),
            )
        )

    # 3. Include any properties present ONLY as Leaflet map markers on the page
    for ext_id, (lat, lng, pop_title, pop_price, pop_img) in leaflet_info.items():
        if ext_id in seen_ids:
            continue
        m_marker = re.search(rf"https?://[^'\"\s<>]*/imovel/[^'\"\s<>]*/{ext_id}", html)
        if not m_marker:
            continue
        m_u = _IMONOV_URL_RE.search(m_marker.group(0))
        if not m_u:
            continue
        p_slug, t_slug, c_slug, b_slug, ttl_slug, _ = m_u.groups()
        r_city, r_state = _city_state_from_slug(c_slug)
        results.append(
            RawScrapedItem(
                agency_slug=agency_slug,
                external_id=ext_id,
                source_url=urljoin(base_url, urlparse(m_marker.group(0)).path),
                raw_purpose=p_slug.replace("-", " "),
                raw_type=t_slug.replace("-", " "),
                title=pop_title or ttl_slug.replace("-", " ").capitalize(),
                raw_city=r_city,
                raw_state=r_state,
                raw_neighborhood=b_slug.replace("-", " "),
                sale_price=pop_price if "venda" in p_slug else None,
                rent_price=pop_price if "locacao" in p_slug else None,
                latitude=lat,
                longitude=lng,
                image_urls=(pop_img,) if pop_img else (),
            )
        )

    return results


def _find_exclusive_card_container(anchor: Tag, target_ext_id: str) -> Tag:
    """Walk up the DOM while the ancestor contains links only to `target_ext_id`."""
    current: Tag = anchor
    for _ in range(8):
        parent = current.parent
        if not isinstance(parent, Tag) or parent.name in ("body", "html"):
            break
        ids_in_parent = {
            m.group(6)
            for a in parent.find_all("a", href=True)
            for m in [_IMONOV_URL_RE.search(str(a["href"]))]
            if m
        }
        if len(ids_in_parent) > 1:
            break
        current = parent
    return current


def _extract_imonov_prices(card: Tag, card_text: str) -> list[float]:
    prices: list[float] = []
    for m in re.finditer(r"R\$\s*([\d.]+(?:,\d{2})?)", card_text):
        val = parse_brl(m.group(1))
        if val is not None and val > 50:
            prices.append(val)
    if prices:
        return list(dict.fromkeys(prices))

    # Bela Morada omits 'R$' inside .property-price
    for el in card.select(".property-price, .valor-imovel, .text-block-11, .text-block-4"):
        txt = el.get_text(" ", strip=True)
        if re.match(r"^[\d.]+,\d{2}$", txt):
            val = parse_brl(txt)
            if val is not None and val > 50:
                prices.append(val)
    return list(dict.fromkeys(prices))


def _extract_imonov_features(
    card: Tag,
    card_text: str,
) -> tuple[float | None, float | None, int | None, int | None, int | None, int | None]:
    area_priv: float | None = None
    area_tot: float | None = None
    beds: int | None = None
    suites: int | None = None
    baths: int | None = None
    parking: int | None = None

    m_priv = re.search(r"(?i)A\.?\s*(?:Privativa|[UÚ]til)\s*:?\s*([\d.,]+)\s*m", card_text)
    if m_priv:
        area_priv = parse_area_m2(m_priv.group(1))
    m_tot = re.search(r"(?i)A\.?\s*Total\s*:?\s*([\d.,]+)\s*m", card_text)
    if m_tot:
        area_tot = parse_area_m2(m_tot.group(1))
    if area_priv is None and area_tot is None:
        m_any = re.search(r"\b([\d.]+(?:,\d+)?)\s*m(?:²|2)(?=\s|$|[|,;])", card_text, re.IGNORECASE)
        if m_any:
            area_tot = parse_area_m2(m_any.group(1))

    # Inspect feature items containing icon images or SVGs
    for feat in card.select(
        ".feature, .property-feature, .box-detalhes-imoveis, .text-block-38, .features li, "
        ".property-features li, [data-feature], li[title], span[title], i[title], svg[aria-label]"
    ):
        f_txt = feat.get_text(" ", strip=True)
        feature_label = " ".join(
            str(feat.get(attr) or "") for attr in ("title", "aria-label", "data-feature")
        )
        num = parse_first_int(f_txt)
        if num is None:
            num = parse_first_int(feature_label)
        img = feat.find("img")
        icon_src = str(img.get("src") or "").lower() if isinstance(img, Tag) else ""
        svg = feat.find("svg")
        svg_vb = str(svg.get("viewbox") or svg.get("viewBox") or "") if isinstance(svg, Tag) else ""

        icon = feat.find(["i", "use"])
        icon_class = " ".join(icon.get("class") or []) if isinstance(icon, Tag) else ""
        icon_href = str(icon.get("href") or icon.get("xlink:href") or "") if isinstance(icon, Tag) else ""
        feat_classes = [str(c).lower() for c in (feat.get("class") or [])]
        feat_class_str = " ".join(feat_classes)
        feature_signature = (
            f"{feat_class_str} {icon_src} {icon_class} {icon_href} {feature_label} {f_txt}".lower()
        )
        if any(k in feature_signature for k in ("suite", "suíte")):
            suites = num if num is not None else suites
        elif any(k in feature_signature for k in ("bed", "quarto", "dorm")) or feat_classes == [
            "text-block-38"
        ]:
            beds = num if num is not None else beds
        elif any(k in feature_signature for k in ("bath", "banh", "shower", "wc")) or svg_vb == "0 0 17 16":
            baths = num if num is not None else baths
        elif any(k in feature_signature for k in ("fa-car", "car.svg", "garag", "vaga")) or (
            "car" in feat_classes
        ):
            parking = num if num is not None else parking
        elif any(k in feature_signature for k in ("metro", "area", "área", "ruler")) and area_tot is None:
            area_tot = parse_area_m2(f_txt)

    return area_priv, area_tot, beds, suites, baths, parking


class ImonovScraper:
    """Async catalog and detail scraper for configured Imonov / Si9 agencies."""

    def __init__(self, http_client: PoliteHttpClient) -> None:
        """Initialize scraper with shared rate-limited HTTP client.

        Args:
            http_client: Shared `PoliteHttpClient` instance.
        """
        self._http = http_client

    async def fetch_catalog(self, agency: AgencyConfig, *, max_pages: int) -> ScrapeBatchResult:
        """Paginate Imonov `/filtro/...` endpoints until no new property IDs appear.

        Args:
            agency: Target Imonov agency configuration.
            max_pages: Safety cap on pages per filter entry URL.

        Returns:
            `ScrapeBatchResult` with collected items and request metrics.
        """
        collected: list[RawScrapedItem] = []
        requests_made = 0
        had_errors = False
        errors: list[str] = []

        for purpose, entry_url in agency.entry_urls:
            seen_in_section: set[str] = set()
            for page in range(1, max_pages + 1):
                page_url = set_imonov_page_number(entry_url, page)
                if page > 1 and page_url == entry_url:
                    break
                try:
                    status, _, html = await self._http.get_text(page_url, agency_slug=agency.slug)
                    requests_made += 1
                except ScraperTransportError as exc:
                    had_errors = True
                    errors.append(str(exc))
                    break

                if status == 404 and page > 1:
                    break
                if status >= 400:
                    had_errors = True
                    errors.append(f"HTTP {status} at {page_url}")
                    break

                page_items = parse_imonov_listing_html(
                    html,
                    agency_slug=agency.slug,
                    base_url=agency.base_url,
                    default_purpose=purpose,
                )
                if page == 1 and not page_items and not has_empty_catalog_notice(html):
                    had_errors = True
                    errors.append(f"Catálogo não reconhecido em {page_url}; verificar integração.")
                    break
                new_items = [it for it in page_items if it.external_id not in seen_in_section]
                if not new_items:
                    break

                for it in new_items:
                    seen_in_section.add(it.external_id)
                    collected.append(it)

                # Habitar returns all markers + large grid on page 1; stop if no next-page link exists
                next_page_url = set_imonov_page_number(entry_url, page + 1)
                parsed_next = urlparse(next_page_url)
                next_target = (
                    f"{parsed_next.path}?{parsed_next.query}" if parsed_next.query else parsed_next.path
                )
                if next_target not in html and next_target.lstrip("/") not in html and len(new_items) < 12:
                    break

        return ScrapeBatchResult(
            items=tuple(collected),
            requests_made=requests_made,
            had_section_errors=had_errors,
            error_summary="; ".join(errors) if errors else None,
        )

    async def fetch_property_detail(self, agency: AgencyConfig, source_url: str) -> PropertyDetailPatch:
        """Fetch supplemental details from an individual Imonov property page.

        Args:
            agency: Target agency configuration.
            source_url: Property detail URL.

        Returns:
            `PropertyDetailPatch` extracted from the page HTML.
        """
        status, _, html = await self._http.get_text(source_url, agency_slug=agency.slug)
        if status >= 400:
            raise ScraperTransportError(agency.slug, source_url, f"HTTP {status}")
        return parse_generic_detail_html(html, agency.base_url)

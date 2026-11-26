"""Scraper adapter for Widesys real-estate websites (Modern & Legacy templates)."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from imob.domain.enums import ListingPurpose
from imob.domain.models import AgencyConfig, PropertyDetailPatch, RawScrapedItem
from imob.errors import ScraperTransportError
from imob.ports import ScrapeBatchResult
from imob.scrapers.http_client import PoliteHttpClient
from imob.scrapers.parsing import (
    extract_image_from_tag,
    has_empty_catalog_notice,
    is_valid_property_photo,
    parse_area_m2,
    parse_brl,
    parse_first_int,
    parse_generic_detail_html,
)

_PROP_PATH_RE = re.compile(r"/(\d+)-([a-z0-9-]+)$", re.IGNORECASE)
_UNAVAILABLE_LABELS = frozenset({"vendido", "vendida", "locado", "locada", "alugado", "alugada"})


def parse_widesys_listing_html(
    html: str,
    *,
    agency_slug: str,
    base_url: str,
    default_purpose: ListingPurpose,
) -> tuple[list[RawScrapedItem], int]:
    """Parse a Widesys listing page (Modern `.featured-thumb` or Legacy Bootstrap 3).

    Args:
        html: Listing page HTML.
        agency_slug: Source agency slug.
        base_url: Base URL of the agency website.
        default_purpose: Section purpose (`aluguel` or `venda`).

    Returns:
        Tuple of `(parsed_items, inferred_page_step)`. An empty pagination widget gives step 0.
    """
    soup = BeautifulSoup(html, "lxml")
    starts = sorted(
        {
            int(m.group(1))
            for a in soup.find_all("a", href=True)
            if isinstance(a.get("href"), str)
            for m in [re.search(r"[?&]start=(\d+)", str(a["href"]))]
            if m and int(m.group(1)) > 0
        }
    )
    if starts:
        page_step = starts[0]
    elif soup.select_one(".pagination") and not soup.select_one(".pagination a[href]"):
        page_step = 0
    else:
        page_step = 21

    modern_cards = soup.select(".featured-thumb")
    if modern_cards:
        items = [
            parsed
            for card in modern_cards
            for parsed in [_parse_modern_card(card, agency_slug, base_url, default_purpose)]
            if parsed is not None
        ]
        return items, page_step

    modern_v2_cards = soup.select(".property-card-modern")
    if modern_v2_cards:
        v2_items = [
            parsed
            for card in modern_v2_cards
            for parsed in [_parse_modern_v2_card(card, agency_slug, base_url, default_purpose)]
            if parsed is not None
        ]
        return v2_items, page_step

    # Fallback: Widesys Legacy Bootstrap 3 template (e.g., realizapb.com.br)
    fav_links = soup.select("a[data-action='favorito']")
    legacy_items = [
        parsed
        for fav in fav_links
        for parsed in [_parse_legacy_card(fav, agency_slug, base_url, default_purpose)]
        if parsed is not None
    ]
    return legacy_items, page_step


def _parse_modern_card(
    card: Tag,
    agency_slug: str,
    base_url: str,
    default_purpose: ListingPurpose,
) -> RawScrapedItem | None:
    prop_href: str | None = None
    ext_id: str | None = None
    slug_tail = ""

    star = card.select_one("a.starmark[data-id]")
    if star and isinstance(star.get("data-id"), str):
        ext_id = str(star["data-id"]).strip()

    for a_tag in card.find_all("a", href=True):
        href = str(a_tag["href"]).strip()
        if not href or href.startswith(("#", "javascript")):
            continue
        m = _PROP_PATH_RE.search(href.split("?")[0].rstrip("/"))
        if m:
            prop_href = urljoin(base_url, href)
            if not ext_id:
                ext_id = m.group(1)
            slug_tail = m.group(2)
            break

    if not prop_href or not ext_id:
        return None

    # Purpose badge (.sale)
    badge_el = card.select_one(".sale")
    badge_txt = badge_el.get_text(" ", strip=True) if badge_el else ""
    offers_rent = bool(re.search(r"(?i)alug|loca", badge_txt))
    offers_sale = bool(re.search(r"(?i)compr|vend", badge_txt))
    purpose = default_purpose
    if offers_rent and offers_sale:
        purpose = ListingPurpose.BOTH
    elif offers_rent:
        purpose = ListingPurpose.RENT
    elif offers_sale:
        purpose = ListingPurpose.SALE

    price_el = card.select_one(".price")
    sale_price, rent_price = None, None
    if price_el and purpose == ListingPurpose.BOTH:
        for amount in price_el.select("[title]"):
            label = str(amount.get("title", "")).lower()
            value = parse_brl(amount.get_text(" ", strip=True))
            if "alug" in label or "loca" in label:
                rent_price = value
            elif "vend" in label or "compr" in label:
                sale_price = value
    elif price_el:
        value = parse_brl(price_el.get_text(" ", strip=True))
        if purpose == ListingPurpose.SALE:
            sale_price = value
        else:
            rent_price = value

    # Type & Title & Address
    type_el = card.select_one(".featured-thumb-data h5")
    raw_type = type_el.get_text(" ", strip=True) if type_el else ""

    title_el = card.select_one(".product-title")
    title = title_el.get_text(" ", strip=True) if title_el else ""
    if not title:
        first_img = card.find("img", title=True)
        if isinstance(first_img, Tag) and isinstance(first_img.get("title"), str):
            title = str(first_img["title"]).strip()

    addr_el = card.select_one(".product-address")
    addr_txt = addr_el.get_text(" ", strip=True) if addr_el else ""
    raw_neighborhood, raw_city = _split_widesys_address(addr_txt)

    # Ref code
    ref_code: str | None = None
    card_text = card.get_text(" ", strip=True)
    m_ref = re.search(r"\bREF\.?\s*([A-Z0-9-]{2,20})\b", card_text, re.IGNORECASE)
    if m_ref:
        ref_code = m_ref.group(1).upper()
    elif slug_tail:
        m_slug_ref = re.search(r"-([a-z]{2,4}\d{1,6})$", slug_tail, re.IGNORECASE)
        if m_slug_ref:
            ref_code = m_slug_ref.group(1).upper()

    # Quantity attributes (<li title="...">)
    bedrooms: int | None = None
    suites: int | None = None
    bathrooms: int | None = None
    parking: int | None = None
    area_priv: float | None = None
    area_built: float | None = None
    area_tot: float | None = None

    for li in card.select(".quantity li"):
        li_title = str(li.get("title") or "").strip().lower()
        li_val_txt = li.get_text(" ", strip=True)
        m_combo = re.search(r"(\d+)\s*dormit.*?(\d+)\s*su[ií]te", li_title)
        if m_combo:
            bedrooms = int(m_combo.group(1))
            suites = int(m_combo.group(2))
            continue
        if "dormit" in li_title or "quarto" in li_title:
            bedrooms = parse_first_int(li_val_txt) or parse_first_int(li_title)
        elif "suíte" in li_title or "suite" in li_title:
            suites = parse_first_int(li_val_txt) or parse_first_int(li_title)
        elif "banheiro" in li_title or "wc" in li_title:
            bathrooms = parse_first_int(li_val_txt)
        elif "vaga" in li_title or "garag" in li_title:
            parking = parse_first_int(li_val_txt)
        elif "privativa" in li_title or "útil" in li_title or "util" in li_title:
            area_priv = parse_area_m2(li_val_txt)
        elif "constru" in li_title:
            area_built = parse_area_m2(li_val_txt)
        elif "total" in li_title or "terreno" in li_title:
            area_tot = parse_area_m2(li_val_txt)

    # Carousel photos
    photos: list[str] = []
    for img in card.select(".carousel-inner img, img"):
        u = extract_image_from_tag(img, base_url)
        if u:
            photos.append(u)

    return RawScrapedItem(
        agency_slug=agency_slug,
        external_id=ext_id,
        source_url=prop_href,
        is_available=not any(text.strip().lower() in _UNAVAILABLE_LABELS for text in card.stripped_strings),
        raw_purpose=purpose.value,
        raw_type=raw_type,
        ref_code=ref_code,
        title=title,
        raw_city=raw_city,
        raw_neighborhood=raw_neighborhood,
        sale_price=sale_price,
        rent_price=rent_price,
        area_private_m2=area_priv,
        area_built_m2=area_built,
        area_total_m2=area_tot,
        bedrooms=bedrooms,
        suites=suites,
        bathrooms=bathrooms,
        parking_spots=parking,
        image_urls=tuple(dict.fromkeys(photos)),
    )


def _parse_modern_v2_card(
    card: Tag,
    agency_slug: str,
    base_url: str,
    default_purpose: ListingPurpose,
) -> RawScrapedItem | None:
    prop_href: str | None = None
    ext_id: str | None = None
    slug_tail = ""

    fav_btn = card.select_one("button[data-id]")
    if fav_btn and isinstance(fav_btn.get("data-id"), str):
        ext_id = str(fav_btn["data-id"]).strip()

    for a_tag in card.find_all("a", href=True):
        href = str(a_tag["href"]).strip()
        if not href or href.startswith(("#", "javascript")):
            continue
        m = _PROP_PATH_RE.search(href.split("?")[0].rstrip("/"))
        if m:
            prop_href = urljoin(base_url, href)
            if not ext_id:
                ext_id = m.group(1)
            slug_tail = m.group(2)
            break

    if not prop_href or not ext_id:
        return None

    badge_el = card.select_one(".badge-category")
    badge_txt = badge_el.get_text(" ", strip=True) if badge_el else ""
    purpose_str = default_purpose.value
    if re.search(r"(?i)alug|loca", badge_txt):
        purpose_str = ListingPurpose.RENT.value
    elif re.search(r"(?i)compr|vend", badge_txt):
        purpose_str = ListingPurpose.SALE.value

    sale_price: float | None = None
    rent_price: float | None = None
    for entry in card.select(".price-entry"):
        lbl_el = entry.select_one(".price-label")
        val_el = entry.select_one(".price-value")
        p_val = parse_brl(val_el.get_text(" ", strip=True)) if val_el else None
        if p_val is None:
            continue
        lbl_txt = lbl_el.get_text(" ", strip=True).lower() if lbl_el else ""
        if "alug" in lbl_txt or "loca" in lbl_txt:
            rent_price = p_val
        elif "vend" in lbl_txt or "compr" in lbl_txt:
            sale_price = p_val
        elif purpose_str == ListingPurpose.RENT.value:
            rent_price = p_val
        else:
            sale_price = p_val

    if sale_price is not None and rent_price is not None:
        purpose_str = ListingPurpose.BOTH.value

    type_el = card.select_one(".property-type")
    raw_type = type_el.get_text(" ", strip=True) if type_el else ""

    title_el = card.select_one(".property-title-modern")
    title = title_el.get_text(" ", strip=True) if title_el else ""

    ref_code: str | None = None
    ref_el = card.select_one(".ref-modern")
    if ref_el:
        m_ref = re.search(r"(?i)REF\s*[:.]?\s*([A-Z0-9*-]{1,20})", ref_el.get_text(" ", strip=True))
        if m_ref:
            ref_code = m_ref.group(1).rstrip("*").upper() or None
    if not ref_code and slug_tail:
        m_slug_ref = re.search(r"-([a-z]{2,4}\d{1,6})$", slug_tail, re.IGNORECASE)
        if m_slug_ref:
            ref_code = m_slug_ref.group(1).upper()

    bedrooms: int | None = None
    suites: int | None = None
    bathrooms: int | None = None
    parking: int | None = None
    area_tot: float | None = None

    for fp in card.select(".feature-point"):
        fp_title = str(fp.get("title") or "").strip().lower()
        fp_txt = fp.get_text(" ", strip=True)
        if "suíte" in fp_title or "suite" in fp_title:
            suites = parse_first_int(fp_txt)
        elif "dormit" in fp_title or "quarto" in fp_title:
            bedrooms = parse_first_int(fp_txt)
        elif "banheiro" in fp_title or "wc" in fp_title:
            bathrooms = parse_first_int(fp_txt)
        elif "vaga" in fp_title or "garag" in fp_title:
            parking = parse_first_int(fp_txt)
        elif "área" in fp_title or "area" in fp_title or "m²" in fp_txt.lower():
            area_tot = parse_area_m2(fp_txt)

    photos: list[str] = []
    for img in card.select(".carousel-inner img, img"):
        u = extract_image_from_tag(img, base_url)
        if u:
            photos.append(u)

    return RawScrapedItem(
        agency_slug=agency_slug,
        external_id=ext_id,
        source_url=prop_href,
        is_available=not any(text.strip().lower() in _UNAVAILABLE_LABELS for text in card.stripped_strings),
        raw_purpose=purpose_str,
        raw_type=raw_type,
        ref_code=ref_code,
        title=title,
        sale_price=sale_price,
        rent_price=rent_price,
        area_total_m2=area_tot,
        bedrooms=bedrooms,
        suites=suites,
        bathrooms=bathrooms,
        parking_spots=parking,
        image_urls=tuple(dict.fromkeys(photos)),
    )


def _parse_legacy_card(
    fav: Tag,
    agency_slug: str,
    base_url: str,
    default_purpose: ListingPurpose,
) -> RawScrapedItem | None:
    ext_id = str(fav.get("data-id") or "").strip()
    rel_link = str(fav.get("data-link") or "").strip()
    if not ext_id or not rel_link:
        return None

    source_url = urljoin(base_url, rel_link)
    title = str(fav.get("data-name") or "").strip()
    img_attr = str(fav.get("data-image") or "").strip()
    photos: list[str] = []
    if img_attr and is_valid_property_photo(urljoin(base_url, img_attr)):
        photos.append(urljoin(base_url, img_attr))

    # Stop before the surrounding grid so adjacent cards cannot supply this property's fields.
    wrapper: Tag = fav
    for _ in range(6):
        if isinstance(wrapper.parent, Tag):
            parent = wrapper.parent
            if len(parent.select("a[data-action='favorito']")) > 1:
                break
            wrapper = parent

    price_val = parse_brl(str(fav.get("data-price") or ""))
    if price_val is None:
        price_a = wrapper.select_one("a.price")
        if price_a:
            price_val = parse_brl(price_a.get_text(" ", strip=True))

    sale_price = price_val if default_purpose == ListingPurpose.SALE else None
    rent_price = price_val if default_purpose == ListingPurpose.RENT else None

    desc_p = wrapper.find("p")
    desc = desc_p.get_text(" ", strip=True) if isinstance(desc_p, Tag) else None

    ref_code: str | None = None
    m_slug = _PROP_PATH_RE.search(rel_link.split("?")[0].rstrip("/"))
    if m_slug:
        m_ref = re.search(r"-([a-z]{2,4}\d{1,6})$", m_slug.group(2), re.IGNORECASE)
        if m_ref:
            ref_code = m_ref.group(1).upper()

    return RawScrapedItem(
        agency_slug=agency_slug,
        external_id=ext_id,
        source_url=source_url,
        is_available=not any(
            text.strip().lower() in _UNAVAILABLE_LABELS for text in wrapper.stripped_strings
        ),
        raw_purpose=default_purpose.value,
        raw_type="",
        ref_code=ref_code,
        title=title,
        description=desc,
        sale_price=sale_price,
        rent_price=rent_price,
        image_urls=tuple(photos),
    )


def _split_widesys_address(addr: str) -> tuple[str | None, str | None]:
    cleaned = re.sub(r"\s+", " ", addr).strip(" -/,.")
    if not cleaned:
        return None, None
    # Common formats: "Centro - Pato Branco/PR" or "Fraron, Pato Branco - PR"
    cleaned = re.sub(r"(?i)[/-]\s*(?:PR|SC|RS)\s*$", "", cleaned).strip()
    for sep in (" - ", ", "):
        if sep in cleaned:
            parts = [p.strip() for p in cleaned.split(sep) if p.strip()]
            if len(parts) >= 2:
                return parts[0], parts[-1]
    return cleaned, None


class WidesysScraper:
    """Async catalog and detail scraper for configured Widesys agencies."""

    def __init__(self, http_client: PoliteHttpClient) -> None:
        """Initialize scraper with shared rate-limited HTTP client.

        Args:
            http_client: Shared `PoliteHttpClient` instance.
        """
        self._http = http_client

    async def fetch_catalog(self, agency: AgencyConfig, *, max_pages: int) -> ScrapeBatchResult:
        """Paginate Widesys `/imoveis-locacao` and `/imoveis-venda` sections.

        Args:
            agency: Target Widesys agency configuration.
            max_pages: Safety limit on pages per section.

        Returns:
            `ScrapeBatchResult` containing collected items and request metrics.
        """
        collected: list[RawScrapedItem] = []
        requests_made = 0
        had_errors = False
        errors: list[str] = []

        for purpose, section_url in agency.entry_urls:
            seen_in_section: set[str] = set()
            offset = 0
            section_step = 21
            for page_idx in range(max_pages):
                # Always pass ?start=0 on page 0 so Joomla resets any session limitstart cookie
                params = dict(parse_qsl(urlparse(section_url).query, keep_blank_values=True))
                params["start"] = str(offset)
                try:
                    status, _, html = await self._http.get_text(
                        section_url,
                        agency_slug=agency.slug,
                        params=params,
                    )
                    requests_made += 1
                except ScraperTransportError as exc:
                    had_errors = True
                    errors.append(str(exc))
                    break

                if status == 404 and page_idx == 0:
                    # Some Widesys agencies only operate one section (e.g., sales only)
                    if len(agency.entry_urls) == 1:
                        had_errors = True
                        errors.append(f"HTTP {status} at {section_url}")
                    break
                if status >= 400:
                    had_errors = True
                    request_url = urlparse(section_url)._replace(query=urlencode(params)).geturl()
                    errors.append(f"HTTP {status} at {request_url}")
                    break

                page_items, step = parse_widesys_listing_html(
                    html,
                    agency_slug=agency.slug,
                    base_url=agency.base_url,
                    default_purpose=purpose,
                )
                if page_idx == 0 and not page_items and not has_empty_catalog_notice(html):
                    had_errors = True
                    errors.append(f"Catálogo não reconhecido em {section_url}; verificar integração.")
                    break
                if page_idx == 0 and step > 0:
                    section_step = step

                new_items = [it for it in page_items if it.external_id not in seen_in_section]
                if not new_items:
                    break

                for it in new_items:
                    seen_in_section.add(it.external_id)
                    collected.append(it)

                if step == 0 or len(page_items) < max(3, section_step // 2):
                    break
                offset += section_step

        return ScrapeBatchResult(
            items=tuple(collected),
            requests_made=requests_made,
            had_section_errors=had_errors,
            error_summary="; ".join(errors) if errors else None,
        )

    async def fetch_property_detail(self, agency: AgencyConfig, source_url: str) -> PropertyDetailPatch:
        """Fetch supplemental details from an individual Widesys property page.

        Args:
            agency: Target agency configuration.
            source_url: Property detail page URL.

        Returns:
            `PropertyDetailPatch` extracted from the page HTML.
        """
        status, _, html = await self._http.get_text(source_url, agency_slug=agency.slug)
        if status >= 400:
            raise ScraperTransportError(agency.slug, source_url, f"HTTP {status}")
        return parse_generic_detail_html(html, agency.base_url)

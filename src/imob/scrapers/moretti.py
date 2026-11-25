"""Scraper adapter for Moretti Imobiliária (WordPress MyHome REST API)."""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from imob.domain.enums import ListingPurpose
from imob.domain.models import AgencyConfig, PropertyDetailPatch, RawScrapedItem
from imob.errors import ScraperParseError, ScraperTransportError
from imob.ports import ScrapeBatchResult
from imob.scrapers.http_client import PoliteHttpClient
from imob.scrapers.parsing import (
    extract_image_from_tag,
    parse_area_m2,
    parse_brl,
    parse_first_int,
    parse_generic_detail_html,
)


def parse_moretti_template_html(
    template_html: str,
    *,
    agency_slug: str,
    base_url: str,
) -> list[RawScrapedItem]:
    """Parse Moretti MyHome listing cards from the JSON `template` HTML payload.

    Args:
        template_html: HTML fragment returned in `myhome/v1/listings` `template` field.
        agency_slug: Source agency slug (`'moretti'`).
        base_url: Agency base URL.

    Returns:
        List of `RawScrapedItem` extracted from all cards.
    """
    soup = BeautifulSoup(template_html, "lxml")
    results: list[RawScrapedItem] = []

    for card in soup.select("a.myhome-listing-card[href]"):
        href = str(card.get("href") or "").strip()
        if not href:
            continue
        source_url = urljoin(base_url, href)
        path_slug = urlparse(source_url).path.rstrip("/").split("/")[-1]
        if not path_slug:
            continue

        # Labels (Locação / Venda / Tipo)
        labels = [lbl.get_text(" ", strip=True) for lbl in card.select(".myhome-listing-card__label")]
        has_rent = any(re.search(r"(?i)loca|alug", lb) for lb in labels)
        has_sale = any(re.search(r"(?i)vend|compr", lb) for lb in labels)

        type_labels = [lb for lb in labels if not re.search(r"(?i)loca|alug|vend|compr|destaque|novo", lb)]
        raw_type = type_labels[0] if type_labels else ""

        name_el = card.select_one(".myhome-listing-card__name")
        title = name_el.get_text(" ", strip=True) if name_el else path_slug.replace("-", " ")

        # Ref code from title ("Ref. 2217", "Ref M563") or URL ("-ref-m563")
        ref_code: str | None = None
        m_ref = re.search(r"(?i)\bRef\.?\s*[:\-]?\s*([A-Za-z0-9-]{2,20})\b", title)
        if m_ref:
            ref_code = m_ref.group(1).upper()
        else:
            m_uref = re.search(r"-ref-([a-z0-9-]+)$", path_slug, re.IGNORECASE)
            if m_uref:
                ref_code = m_uref.group(1).upper()

        ext_id = ref_code or path_slug[:80]

        addr_sel = (
            ".myhome-listing-card-additional-attribute--address "
            ".myhome-listing-card-additional-attribute__value"
        )
        addr_el = card.select_one(addr_sel)
        addr_txt = addr_el.get_text(" ", strip=True) if addr_el else None

        val_el = card.select_one(".myhome-listing-card-main-value")
        val_txt = val_el.get_text(" ", strip=True) if val_el else ""
        price_val = parse_brl(val_txt)

        if "/m" in val_txt.lower() or "mês" in val_txt.lower():
            has_rent = True

        if has_rent and has_sale:
            raw_purpose = ListingPurpose.BOTH.value
            sale_price = price_val if (price_val and price_val >= 80_000) else None
            rent_price = price_val if (price_val and price_val < 80_000) else None
        elif has_rent:
            raw_purpose = ListingPurpose.RENT.value
            sale_price = None
            rent_price = price_val
        else:
            raw_purpose = ListingPurpose.SALE.value
            sale_price = price_val
            rent_price = None

        # Attributes (beds, baths, parking, area) via SVG icon signatures + units
        beds: int | None = None
        baths: int | None = None
        parking: int | None = None
        area_tot: float | None = None

        for attr in card.select(".myhome-attribute"):
            v_el = attr.select_one(".myhome-attribute__value")
            if not v_el:
                continue
            v_txt = v_el.get_text(" ", strip=True)
            if "m²" in v_txt.lower() or "m2" in v_txt.lower():
                area_tot = parse_area_m2(v_txt)
                continue

            svg = attr.find("svg")
            path_tag = svg.find("path") if isinstance(svg, Tag) else None
            d_attr = str(path_tag.get("d") or "") if isinstance(path_tag, Tag) else ""
            vb_attr = str(svg.get("viewBox") or svg.get("viewbox") or "") if isinstance(svg, Tag) else ""
            num = parse_first_int(v_txt)
            if num is None:
                continue

            if "640 512" in vb_attr and d_attr.startswith("M32 32"):
                beds = num
            elif "512 512" in vb_attr and d_attr.startswith("M96 77.3"):
                baths = num
            elif d_attr.startswith("M135.2") or "512 512" in vb_attr:
                if baths is None:
                    baths = num
                else:
                    parking = num
            elif parking is None:
                parking = num

        photos: list[str] = []
        for img in card.select("img"):
            u = extract_image_from_tag(img, base_url)
            if u:
                photos.append(u)

        results.append(
            RawScrapedItem(
                agency_slug=agency_slug,
                external_id=ext_id,
                source_url=source_url,
                raw_purpose=raw_purpose,
                raw_type=raw_type,
                ref_code=ref_code,
                title=title,
                street=addr_txt,
                sale_price=sale_price,
                rent_price=rent_price,
                area_total_m2=area_tot,
                bedrooms=beds,
                bathrooms=baths,
                parking_spots=parking,
                image_urls=tuple(dict.fromkeys(photos)),
            )
        )

    return results


class MorettiScraper:
    """Async catalog & detail scraper for Moretti Imobiliária (`myhome/v1/listings`)."""

    def __init__(self, http_client: PoliteHttpClient) -> None:
        """Initialize scraper with shared rate-limited HTTP client.

        Args:
            http_client: Shared `PoliteHttpClient` instance.
        """
        self._http = http_client

    async def fetch_catalog(self, agency: AgencyConfig, *, max_pages: int) -> ScrapeBatchResult:
        """Fetch Moretti listings in bulk via the MyHome REST endpoint.

        Args:
            agency: Target Moretti agency configuration.
            max_pages: Safety limit on pagination requests.

        Returns:
            `ScrapeBatchResult` with collected items and request metrics.
        """
        endpoint = agency.entry_urls[0][1]
        collected: list[RawScrapedItem] = []
        seen_ids: set[str] = set()
        requests_made = 0

        for page in range(1, max_pages + 1):
            status, _, body = await self._http.get_text(
                endpoint,
                agency_slug=agency.slug,
                params={
                    "limit": "300",
                    "page": str(page),
                    "template": "templates/widgets/general/search/partials/search_results_card",
                },
            )
            requests_made += 1
            if status >= 400:
                raise ScraperTransportError(agency.slug, endpoint, f"HTTP {status}")
            try:
                payload = json.loads(body)
            except ValueError as exc:
                raise ScraperParseError(agency.slug, f"Invalid JSON from listings API: {exc}") from exc

            tpl = str(payload.get("template") or "")
            batch = parse_moretti_template_html(
                tpl,
                agency_slug=agency.slug,
                base_url=agency.base_url,
            )
            new_items = [it for it in batch if it.external_id not in seen_ids]
            for it in new_items:
                seen_ids.add(it.external_id)
                collected.append(it)

            total_reported = int(payload.get("count") or 0)
            if not new_items or len(seen_ids) >= total_reported or page >= 3:
                break

        return ScrapeBatchResult(items=tuple(collected), requests_made=requests_made)

    async def fetch_property_detail(self, agency: AgencyConfig, source_url: str) -> PropertyDetailPatch:
        """Fetch supplemental details from an individual Moretti property page.

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

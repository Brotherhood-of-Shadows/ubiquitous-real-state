"""Scraper adapter for Imobiliária Uniq (Apresenta.me platform)."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from imob.domain.enums import ListingPurpose
from imob.domain.models import AgencyConfig, PropertyDetailPatch, RawScrapedItem
from imob.errors import ScraperTransportError
from imob.ports import ScrapeBatchResult
from imob.scrapers.http_client import PoliteHttpClient
from imob.scrapers.parsing import (
    extract_image_from_tag,
    parse_area_m2,
    parse_brl,
    parse_first_int,
    parse_generic_detail_html,
)


def parse_uniq_cards_html(
    html: str,
    *,
    agency_slug: str,
    base_url: str,
    default_purpose: ListingPurpose,
) -> list[RawScrapedItem]:
    """Parse Apresenta.me `div.LI_Imovel` property cards.

    Args:
        html: HTML document or AJAX grid fragment containing `.LI_Imovel` blocks.
        agency_slug: Source agency slug (`'uniq'`).
        base_url: Agency base URL.
        default_purpose: Section purpose (`aluguel` or `venda`).

    Returns:
        List of parsed `RawScrapedItem` entries.
    """
    soup = BeautifulSoup(html, "lxml")
    results: list[RawScrapedItem] = []

    for card in soup.select("div.LI_Imovel"):
        ext_id = str(card.get("id") or "").strip()
        b_id_el = card.select_one("span.buildingId")
        if b_id_el and b_id_el.get_text(strip=True):
            ext_id = b_id_el.get_text(strip=True)

        link_el = card.select_one("a.Title[href], a.ImovelLinkClick[href]")
        if not ext_id or not link_el or not isinstance(link_el.get("href"), str):
            continue
        source_url = urljoin(base_url, str(link_el["href"]).strip())

        title_el = card.select_one("a.Title")
        title = title_el.get_text(" ", strip=True) if title_el else ""

        ref_el = card.select_one("span.ImovelId span.id")
        ref_code = ref_el.get_text(strip=True) if ref_el else None

        subcat_el = card.select_one("span.SubCategoria")
        cat_el = card.select_one("div.Categoria")
        raw_type = (
            subcat_el.get_text(" ", strip=True)
            if subcat_el and subcat_el.get_text(strip=True)
            else (cat_el.get_text(" ", strip=True) if cat_el else "")
        )

        # Structured address fields provided directly by Apresenta.me
        cep_el = card.select_one("span.Endereco span.Cep")
        rua_el = card.select_one("span.Endereco span.Rua")
        num_el = card.select_one("span.Endereco span.Numero")
        bairro_el = card.select_one("span.Endereco span.Bairro")
        cidade_el = card.select_one("span.Endereco span.cidade")
        uf_el = card.select_one("span.Endereco span.Estado")

        postal_code = re.sub(r"(?i)^cep\s*:?\s*", "", cep_el.get_text(strip=True)) if cep_el else None
        street = rua_el.get_text(" ", strip=True) if rua_el else None
        street_num = (
            re.sub(r"(?i)^n[°ºo.]*\s*:?\s*", "", num_el.get_text(" ", strip=True)).strip()
            if num_el
            else None
        )
        raw_nb = bairro_el.get_text(" ", strip=True) if bairro_el else None
        raw_city = cidade_el.get_text(" ", strip=True) if cidade_el else None
        raw_state = uf_el.get_text(" ", strip=True) if uf_el else None

        # Prices & extra fees
        sale_price: float | None = None
        rent_price: float | None = None
        condo_fee: float | None = None
        iptu_fee: float | None = None

        for val_span in card.select(".ImovelValor"):
            val_num_el = val_span.select_one(".Valor")
            desc_el = val_span.select_one(".ValorDesc")
            if not val_num_el:
                continue
            parsed_v = parse_brl(val_num_el.get_text(" ", strip=True))
            if parsed_v is None:
                continue
            desc_txt = desc_el.get_text(" ", strip=True).lower() if desc_el else ""
            if "condom" in desc_txt:
                condo_fee = parsed_v
            elif "iptu" in desc_txt:
                iptu_fee = parsed_v
            elif "alug" in desc_txt or "loca" in desc_txt:
                rent_price = parsed_v
            elif "vend" in desc_txt or "compr" in desc_txt:
                sale_price = parsed_v
            elif default_purpose == ListingPurpose.RENT and rent_price is None:
                rent_price = parsed_v
            elif default_purpose == ListingPurpose.SALE and sale_price is None:
                sale_price = parsed_v

        # Physical summary attributes
        area_priv: float | None = None
        area_built: float | None = None
        area_tot: float | None = None
        beds: int | None = None
        suites: int | None = None
        baths: int | None = None
        parking: int | None = None

        for item_el in card.select("span.ResumoItem"):
            classes = " ".join(item_el.get("class") or []).upper()
            val_el = item_el.select_one(".val")
            val_txt = val_el.get_text(" ", strip=True) if val_el else item_el.get_text(" ", strip=True)
            if "AREA_PRIVATIVA" in classes or "AREA_UTIL" in classes:
                area_priv = parse_area_m2(val_txt)
            elif "AREA_CONSTRUIDA" in classes:
                area_built = parse_area_m2(val_txt)
            elif "AREA_TOTAL" in classes or "AREA_TERRENO" in classes:
                area_tot = parse_area_m2(val_txt)
            elif "SUITE" in classes:
                suites = parse_first_int(val_txt)
            elif "DORM" in classes or "QUARTO" in classes:
                beds = parse_first_int(val_txt)
            elif "BANH" in classes or "WC" in classes:
                baths = parse_first_int(val_txt)
            elif "GARAG" in classes or "VAGA" in classes:
                parking = parse_first_int(val_txt)

        desc_div = card.select_one(".ResumoDescritivo")
        desc = desc_div.get_text(" ", strip=True) if desc_div and desc_div.get_text(strip=True) else None

        wa_a = card.select_one(".buttomWhatsapp a[href]")
        wa_url = (
            str(wa_a["href"]).strip()
            if isinstance(wa_a, Tag) and isinstance(wa_a.get("href"), str)
            else None
        )

        photos: list[str] = []
        for img in card.select(".ImovelImagesSlider img, img.BannerImage"):
            u = extract_image_from_tag(img, base_url)
            if u:
                photos.append(u)

        purpose_raw = default_purpose.value
        if sale_price is not None and rent_price is not None:
            purpose_raw = ListingPurpose.BOTH.value

        results.append(
            RawScrapedItem(
                agency_slug=agency_slug,
                external_id=ext_id,
                source_url=source_url,
                raw_purpose=purpose_raw,
                raw_type=raw_type,
                ref_code=ref_code,
                title=title,
                description=desc,
                raw_city=raw_city,
                raw_state=raw_state,
                raw_neighborhood=raw_nb,
                street=street,
                street_number=street_num,
                postal_code=postal_code,
                sale_price=sale_price,
                rent_price=rent_price,
                condo_fee=condo_fee,
                iptu_fee=iptu_fee,
                area_private_m2=area_priv,
                area_built_m2=area_built,
                area_total_m2=area_tot,
                bedrooms=beds,
                suites=suites,
                bathrooms=baths,
                parking_spots=parking,
                whatsapp_url=wa_url,
                image_urls=tuple(dict.fromkeys(photos)),
            )
        )

    return results


class UniqScraper:
    """Async catalog & detail scraper for Imobiliária Uniq (`apresenta.me`)."""

    def __init__(self, http_client: PoliteHttpClient) -> None:
        """Initialize scraper with shared rate-limited HTTP client.

        Args:
            http_client: Shared `PoliteHttpClient` instance.
        """
        self._http = http_client

    async def fetch_catalog(self, agency: AgencyConfig, *, max_pages: int) -> ScrapeBatchResult:
        """Fetch Uniq `/imoveis/aluguel` and `/imoveis/venda` plus AJAX `POST /busca` pages.

        Args:
            agency: Target Uniq agency configuration.
            max_pages: Safety limit on AJAX pagination batches.

        Returns:
            `ScrapeBatchResult` containing collected items and request metrics.
        """
        collected: list[RawScrapedItem] = []
        requests_made = 0
        had_errors = False
        errors: list[str] = []

        for purpose, section_url in agency.entry_urls:
            seen_in_section: set[str] = set()
            try:
                status, final_url, html = await self._http.get_text(section_url, agency_slug=agency.slug)
                requests_made += 1
            except ScraperTransportError as exc:
                had_errors = True
                errors.append(str(exc))
                continue

            if status >= 400:
                had_errors = True
                errors.append(f"HTTP {status} at {section_url}")
                continue

            initial_items = parse_uniq_cards_html(
                html,
                agency_slug=agency.slug,
                base_url=agency.base_url,
                default_purpose=purpose,
            )
            for it in initial_items:
                seen_in_section.add(it.external_id)
                collected.append(it)

            sess_m = re.search(r'"SESSION_ID"\s*:\s*"([^"]+)"', html)
            dom_m = re.search(r'"DOMAIN"\s*:\s*"([^"]+)"', html)
            if not sess_m or "BtnShowMoreImovel" not in html:
                continue

            session_id = sess_m.group(1)
            domain = dom_m.group(1) if dom_m else "imobiliariauniq.com.br"
            fin_code = "rent" if purpose == ListingPurpose.RENT else "sale"
            busca_url = urljoin(final_url, "/busca")

            limit_offset = len(initial_items) or 18
            for _ in range(max_pages - 1):
                try:
                    data = await self._http.post_json(
                        busca_url,
                        agency_slug=agency.slug,
                        form_data={
                            "SESSION_ID": session_id,
                            "DOMAIN": domain,
                            "url": "../../",
                            "SHOW_SEARCH": "true",
                            "FORM_DATA[finalidade][0]": fin_code,
                            "GRID_ONLY": "1",
                            "LIMIT": str(limit_offset),
                        },
                        headers={
                            "X-Requested-With": "XMLHttpRequest",
                            "Referer": final_url,
                        },
                    )
                    requests_made += 1
                except ScraperTransportError as exc:
                    had_errors = True
                    errors.append(str(exc))
                    break

                frag_html = str(data.get("html") or "")
                if not frag_html.strip():
                    break
                batch_items = parse_uniq_cards_html(
                    frag_html,
                    agency_slug=agency.slug,
                    base_url=agency.base_url,
                    default_purpose=purpose,
                )
                new_items = [it for it in batch_items if it.external_id not in seen_in_section]
                if not new_items:
                    break
                for it in new_items:
                    seen_in_section.add(it.external_id)
                    collected.append(it)
                limit_offset += len(batch_items)
                if "BtnShowMoreImovel" not in frag_html:
                    break

        return ScrapeBatchResult(
            items=tuple(collected),
            requests_made=requests_made,
            had_section_errors=had_errors,
            error_summary="; ".join(errors) if errors else None,
        )

    async def fetch_property_detail(self, agency: AgencyConfig, source_url: str) -> PropertyDetailPatch:
        """Fetch supplemental details from an individual Uniq property page.

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

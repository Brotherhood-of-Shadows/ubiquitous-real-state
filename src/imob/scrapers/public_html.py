"""Read public HTML catalogs using property links, card text, and published pagination."""

from __future__ import annotations

import json
import re
import unicodedata
from contextlib import suppress
from urllib.parse import parse_qs, unquote, urljoin, urlparse, urlunparse

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
    parse_generic_detail_html,
)

_LINK_SELECTOR = "a[href], [data-href], [data-url], [onclick]"
_PROPERTY_PATH = re.compile(
    r"^/(?:imovel/([\w-]{1,96})(?:/|$)|ver-imovel/(\d{1,20})(?:-|/|$)|show/(\d{1,20})(?:/|$))"
)
_PAGING_KEYS = frozenset({"pagina", "page", "pag", "pg", "p", "start", "offset"})
_MORE_LABELS = frozenset({"ver mais", "carregar mais", "mostrar mais"})
_NEXT_LABELS = _MORE_LABELS | {"proxima", "proximo", "next", "seguinte", ">", "›", "»"}
_UNAVAILABLE = frozenset({"vendido", "vendida", "locado", "locada", "alugado", "alugada"})


def _fold(text: str) -> str:
    # Preserve decimal separators and turn superscript ² into 2.
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def _local_url(value: str, page_url: str) -> str | None:
    url = urljoin(page_url, value.strip())
    parsed = urlparse(url)
    base = urlparse(page_url)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username is not None
        or parsed.netloc.lower().removeprefix("www.") != base.netloc.lower().removeprefix("www.")
    ):
        return None
    return urlunparse(parsed._replace(fragment=""))


def _element_url(tag: Tag, page_url: str) -> str | None:
    for attr in ("href", "data-href", "data-url"):
        value = tag.get(attr)
        if isinstance(value, str) and value and not value.startswith(("#", "javascript:")):
            return _local_url(value, page_url)
    # Read a literal navigation URL; never execute JavaScript from a remote page.
    for value in re.findall(r"['\"]((?:https?://|/)[^'\"\s<>]+)['\"]", str(tag.get("onclick", ""))):
        if url := _local_url(value, page_url):
            return url
    return None


def _property_link(tag: Tag, page_url: str) -> tuple[str, str] | None:
    url = _element_url(tag, page_url)
    match = _PROPERTY_PATH.match(urlparse(url).path) if url else None
    if match and url:
        external_id = match.group(1) or match.group(2) or match.group(3)
        if any(char.isdigit() for char in external_id):
            return external_id, url
    return None


def _card_container(anchor: Tag, external_id: str, page_url: str) -> Tag:
    card = anchor
    while isinstance(card.parent, Tag):
        parent = card.parent
        if parent.name in {"body", "html", "main", "section", "[document]"}:
            break
        if parent.select_one("header, footer, nav, form, .footer, .header, [role='navigation']"):
            break
        ids = {
            link[0]
            for tag in parent.select(_LINK_SELECTOR)
            if (link := _property_link(tag, page_url)) is not None
        }
        if ids != {external_id}:
            break
        card = parent
    return card


def _count(text: str, label: str) -> int | None:
    match = re.search(rf"\b(\d{{1,3}})\s*(?:{label})\b", text)
    if not match:
        match = re.search(rf"\b(?:{label})\s*:?\s*(\d{{1,3}})\b", text)
    return int(match.group(1)) if match else None


def _area(text: str, label: str) -> float | None:
    match = re.search(rf"([\d.,]+)\s*m2\s*(?:area\s+)?(?:{label})\b", text)
    if not match:
        match = re.search(rf"\b(?:area\s+)?(?:{label})\s*:?\s*([\d.,]+)\s*m2\b", text)
    return parse_area_m2(match.group(1)) if match else None


def _location(card: Tag, text: str, source_url: str = "") -> tuple[str | None, str | None, str | None]:
    locality = card.select_one("[itemprop='addressLocality']")
    if locality:
        return str(locality.get("content") or locality.get_text(" ", strip=True)), None, None
    for attr in ("data-city", "data-cidade"):
        value = card.get(attr)
        if isinstance(value, str) and value.strip() and not value.isdigit():
            return value.strip(), None, None
    m_show = re.match(r"^/show/\d+/([^/?#]+)", urlparse(source_url).path)
    if m_show:
        slug = unquote(m_show.group(1))
        with suppress(UnicodeError):
            slug = slug.encode("latin1").decode("utf-8")
        m_tail = re.search(r"-(pato-branco|[a-z]+)-(pr|sc|rs)$", _fold(slug))
        if m_tail:
            city = (
                "Pato Branco"
                if m_tail.group(1) == "pato-branco"
                else m_tail.group(1).replace("-", " ").title()
            )
            prefix = slug[: m_tail.start()].strip("-")
            prefix = re.sub(
                r"(?i)^(?:sala-comercial|apartamento|sobrado|terrenos?|chacaras?|chácara?s?|casa|lote)-?",
                "",
                prefix,
            ).strip("-")
            bairro_match = re.search(r"(?i)\bbairro\s*:\s*([^\n\r📍📐\d]+)", text)
            neighborhood = (
                prefix.replace("-", " ").title()
                if prefix
                else (bairro_match.group(1).strip() if bairro_match else None)
            )
            return city, m_tail.group(2).upper(), neighborhood
    match = re.search(r"(?:^|[-,\n])\s*([\w .]+?)\s*/\s*([A-Z]{2})\b", text)
    if match:
        before = text[: match.start()].splitlines()[-1:]
        parts = before[0].rsplit(" - ", 1) if before else []
        return match.group(1).strip(), match.group(2), parts[-1].strip() if parts else None
    if re.search(r"\bpato[ -]+branco\b", _fold(text)):
        return "Pato Branco", None, None
    return None, None, None


def _parse_card(
    card: Tag,
    anchor: Tag,
    external_id: str,
    source_url: str,
    agency_slug: str,
    base_url: str,
    default_purpose: ListingPurpose,
) -> RawScrapedItem:
    text = card.get_text("\n", strip=True)
    folded = _fold(card.get_text(" ", strip=True))
    heading = card.select_one("a.prop-title, h2, h3, h4, h5, h6")
    title = heading.get_text(" ", strip=True) if heading else anchor.get_text(" ", strip=True)
    title = re.split(r"R\$|\d[\d.,]*\s*m[²2]", title, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    title = re.sub(r"^(?:[àa] venda|loca[çc][ãa]o|aluguel|\+\s*\d+\s*fotos)\s*", "", title, flags=re.I)
    if not title:
        title = urlparse(source_url).path.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")

    purpose_text = folded + " " + urlparse(source_url).path.lower()
    rent = bool(re.search(r"\b(?:alug\w*|locacao)\b", purpose_text))
    sale = bool(re.search(r"\b(?:venda|vender|comprar)\b", purpose_text))
    if rent and sale:
        purpose = ListingPurpose.BOTH
    elif rent:
        purpose = ListingPurpose.RENT
    elif sale:
        purpose = ListingPurpose.SALE
    else:
        purpose = default_purpose
    prices = [
        value
        for match in re.finditer(r"r\$\s*([\d.,]+)", folded)
        if not re.search(
            r"(?:condominio|iptu|seguro)\s*:?\s*$", folded[max(0, match.start() - 20) : match.start()]
        )
        if (value := parse_brl(match.group(1))) is not None
    ]
    sale_price = max(prices) if prices and purpose != ListingPurpose.RENT else None
    rent_price = min(prices) if prices and purpose == ListingPurpose.RENT else None
    if purpose == ListingPurpose.BOTH:
        # A single price on a dual-purpose card has no reliable transaction label.
        sale_price = max(prices) if len(prices) >= 2 else None
        rent_price = min(prices) if len(prices) >= 2 else None
    city, state, neighborhood = _location(card, text, source_url)
    type_match = re.search(
        r"\b(?:casa em condominio|sala comercial|area rural|apartamento|sobrado|casa|terrenos?|lote|"
        r"chacaras?|fazenda|galpao|barracao|cobertura|kitnet|comercial)\b",
        folded,
    )
    photos = [url for img in card.find_all("img") if (url := extract_image_from_tag(img, base_url))]
    condo = re.search(r"condominio\s*:?\s*(?:r\$\s*)?([\d.,]+)", folded)
    iptu = re.search(r"\biptu\s*:?\s*(?:r\$\s*)?([\d.,]+)", folded)
    reference = re.search(r"\b(?:ref\.?|cod\.?|codigo|identificador)\s*:?\s*([a-z0-9-]+)", folded)
    area_priv = _area(folded, r"privativa|util")
    area_built = _area(folded, r"construida")
    area_tot = _area(folded, r"total|do lote|do terreno|terreno")
    if (
        area_priv is None
        and area_built is None
        and area_tot is None
        and (any_area := re.search(r"\b(\d[\d.,]*)\s*m2\b", folded))
    ):
        area_tot = parse_area_m2(any_area.group(1))
    desc_el = card.select_one(".descricao_imoveis")
    description = desc_el.get_text("\n", strip=True) if desc_el else None
    return RawScrapedItem(
        agency_slug=agency_slug,
        external_id=external_id,
        source_url=source_url,
        title=title,
        raw_type=type_match.group(0) if type_match else "",
        raw_purpose=purpose.value,
        raw_city=city,
        raw_state=state,
        raw_neighborhood=neighborhood,
        ref_code=reference.group(1).upper() if reference else external_id,
        is_available=not any(_fold(value).strip() in _UNAVAILABLE for value in card.stripped_strings),
        sale_price=sale_price,
        rent_price=rent_price,
        condo_fee=parse_brl(condo.group(1)) if condo else None,
        iptu_fee=parse_brl(iptu.group(1)) if iptu else None,
        bedrooms=_count(folded, r"quartos?|dorm(?:s|itorios?)?(?:\(s\))?"),
        suites=_count(folded, r"suites?"),
        bathrooms=_count(folded, r"banheiros?|wc"),
        parking_spots=_count(folded, r"vagas?|garagem|garagens|carros?|veiculos?"),
        area_private_m2=area_priv,
        area_built_m2=area_built,
        area_total_m2=area_tot,
        description=description,
        image_urls=tuple(dict.fromkeys(photos)),
    )


def _pagination(soup: BeautifulSoup, page_url: str, link_base: str) -> tuple[str | None, bool]:
    current_query = parse_qs(urlparse(page_url).query)
    current = next(
        (
            int(v[0])
            for k, v in current_query.items()
            if k in _PAGING_KEYS and v[0].isdigit() and len(v[0]) <= 6
        ),
        1,
    )
    numbered: list[tuple[int, str]] = []
    explicit: list[str] = []
    unresolved = False
    for tag in soup.select("a, button, input[type='button']"):
        if (
            tag.has_attr("disabled")
            or tag.get("aria-disabled") == "true"
            or tag.find_parent(class_="disabled")
        ):
            continue
        label = _fold(
            str(tag.get("aria-label") or tag.get("value") or tag.get_text(" ", strip=True))
        ).strip()
        url = _element_url(tag, link_base)
        if url and url != page_url and _property_link(tag, link_base) is None:
            if re.search(r"condominio|empreendimento", urlparse(url).path):
                continue
            query = parse_qs(urlparse(url).query)
            if (
                label.isdigit()
                and len(label) <= 6
                and any(k in _PAGING_KEYS for k in query)
                and int(label) > current
            ):
                numbered.append((int(label), url))
            elif label in _NEXT_LABELS or "next" in (tag.get("rel") or []):
                explicit.append(url)
        elif label in _MORE_LABELS:
            unresolved = True
    for li in soup.select("#paginacao li[data-valor]"):
        valor = str(li.get("data-valor") or "").strip()
        if valor.isdigit() and len(valor) <= 6 and int(valor) > current:
            numbered.append((int(valor), f"{page_url.split('?', 1)[0]}?pagina={int(valor)}"))
    text = _fold(soup.get_text(" ", strip=True))
    count = re.search(r"\b(\d{1,6})\s+de\s+(\d{1,6})\b", text)
    next_url = explicit[0] if explicit else min(numbered)[1] if numbered else None
    if not next_url and count and 0 < int(count.group(1)) < int(count.group(2)):
        unresolved = True
    return next_url, unresolved and next_url is None


def _parse_window_imoveis(
    text: str,
    *,
    agency_slug: str,
    base_url: str,
    default_purpose: ListingPurpose,
) -> list[RawScrapedItem]:
    match = re.search(r"window\.IMOVEIS\s*=\s*(\[.*\])\s*;", text, re.DOTALL)
    if not match:
        return []
    body = re.sub(r"(?m)\s+//[^\n]*$", "", match.group(1))
    body = re.sub(r"(?m)^(\s*)([A-Za-z_]\w*)\s*:", '\\1"\\2":', body)
    body = re.sub(r",\s*([}\]])", r"\1", body)
    try:
        entries = json.loads(body)
    except ValueError:
        return []
    items: list[RawScrapedItem] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        code = str(entry.get("codigo") or "").strip()
        if not code:
            continue
        pagina = str(entry.get("pagina") or "").strip()
        source_url = urljoin(base_url, pagina or f"imovel.html?cod={code}")
        specs = [
            f"{pair[0]}: {pair[1]}"
            for pair in (entry.get("specs") or [])
            if isinstance(pair, list) and len(pair) == 2
        ]
        folded_specs = _fold(
            " | ".join([str(entry.get("area") or ""), str(entry.get("detalhe") or ""), *specs])
        )
        preco_label = _fold(str(entry.get("precoLabel") or ""))
        sale_price = None if "entrada" in preco_label else parse_brl(str(entry.get("preco") or ""))
        city_raw = str(entry.get("cidade") or "").strip()
        city = "Pato Branco" if "pato branco" in _fold(city_raw) else (city_raw or None)
        state = "PR" if re.search(r"\bpr\b", _fold(city_raw)) else None
        photos = [
            urljoin(base_url, str(p).strip())
            for p in [entry.get("capa"), *(entry.get("galeria") or [])]
            if isinstance(p, str) and p.strip()
        ]
        desc_parts = [str(p).strip() for p in (entry.get("descricao") or []) if str(p).strip()]
        items.append(
            RawScrapedItem(
                agency_slug=agency_slug,
                external_id=code,
                source_url=source_url,
                title=str(entry.get("titulo") or code).strip(),
                raw_type=str(entry.get("tipo") or "").strip(),
                raw_purpose=default_purpose.value,
                raw_city=city,
                raw_state=state,
                raw_neighborhood=str(entry.get("bairro") or "").strip() or None,
                ref_code=code,
                is_available=bool(entry.get("disponivel", True)),
                sale_price=sale_price,
                bedrooms=_count(folded_specs, r"quartos?|dorm(?:s|itorios?)?(?:\(s\))?"),
                suites=_count(folded_specs, r"suites?"),
                bathrooms=_count(folded_specs, r"banheiros?|wc"),
                parking_spots=_count(folded_specs, r"vagas?|garagem|garagens|carros?|veiculos?"),
                area_private_m2=_area(folded_specs, r"privativa|util"),
                area_built_m2=_area(folded_specs, r"construida"),
                area_total_m2=_area(folded_specs, r"total|do lote|do terreno|terreno")
                or parse_area_m2(str(entry.get("area") or "")),
                description="\n\n".join(desc_parts) or None,
                image_urls=tuple(dict.fromkeys(photos)),
                detail_complete=True,
            )
        )
    return items


def parse_public_catalog_html(
    html: str,
    *,
    agency_slug: str,
    base_url: str,
    page_url: str,
    default_purpose: ListingPurpose,
) -> tuple[list[RawScrapedItem], str | None, bool]:
    """Return cards, the published next-page URL, and whether loading needs unresolved JavaScript.

    Only same-site property links and literal navigation URLs are accepted. Unknown
    cities remain unknown so the existing strict city filter can reject them.
    """
    if "window.IMOVEIS" in html:
        return (
            _parse_window_imoveis(
                html,
                agency_slug=agency_slug,
                base_url=base_url,
                default_purpose=default_purpose,
            ),
            None,
            False,
        )
    soup = BeautifulSoup(html, "lxml")
    base = soup.select_one("base[href]")
    link_base = _local_url(str(base["href"]), page_url) if base else page_url
    link_base = link_base or page_url
    for noisy in soup.select(
        "script, style, header, footer, nav, noscript, .related, .similares, .small-property-list"
    ):
        noisy.decompose()
    links: dict[str, list[tuple[Tag, str]]] = {}
    for tag in soup.select(_LINK_SELECTOR):
        if link := _property_link(tag, link_base):
            links.setdefault(link[0], []).append((tag, link[1]))
    items: list[RawScrapedItem] = []
    for external_id, anchors in links.items():
        anchor, source_url = max(anchors, key=lambda pair: len(pair[0].get_text(" ", strip=True)))
        card = _card_container(anchor, external_id, link_base)
        items.append(
            _parse_card(card, anchor, external_id, source_url, agency_slug, base_url, default_purpose)
        )
    next_url, unresolved = _pagination(soup, page_url, link_base)
    return items, next_url, unresolved


class PublicHtmlScraper:
    """Collect paginated HTML catalogs without fetching each property's detail page."""

    def __init__(self, http_client: PoliteHttpClient) -> None:
        """Share the application's polite HTTP client."""
        self._http = http_client

    async def fetch_catalog(self, agency: AgencyConfig, *, max_pages: int) -> ScrapeBatchResult:
        """Follow published catalog pages, reporting truncation and unsupported dynamic loading."""
        collected: list[RawScrapedItem] = []
        errors: list[str] = []
        requests = 0
        for purpose, entry_url in agency.entry_urls:
            page_url: str | None = entry_url
            seen_pages: set[str] = set()
            seen_ids: set[str] = set()
            for _ in range(max_pages):
                if page_url is None:
                    break
                if page_url in seen_pages:
                    errors.append(f"Paginação repetida em {page_url}.")
                    break
                seen_pages.add(page_url)
                requests += 1
                try:
                    if agency.catalog_form:
                        form_data = dict(agency.catalog_form)
                        for key, values in parse_qs(urlparse(page_url).query).items():
                            if key in _PAGING_KEYS and values:
                                form_data[key] = values[0]
                        status, final_url, html = await self._http.post_text(
                            page_url.split("?", 1)[0],
                            agency_slug=agency.slug,
                            form_data=form_data,
                        )
                    else:
                        status, final_url, html = await self._http.get_text(
                            page_url, agency_slug=agency.slug
                        )
                    if status >= 400 or not _local_url(final_url, agency.base_url):
                        raise ScraperTransportError(
                            agency.slug, page_url, f"HTTP {status} ou domínio inesperado"
                        )
                except ScraperTransportError as exc:
                    errors.append(str(exc))
                    break
                items, next_url, unresolved = parse_public_catalog_html(
                    html,
                    agency_slug=agency.slug,
                    base_url=agency.base_url,
                    page_url=page_url if agency.catalog_form else final_url,
                    default_purpose=purpose,
                )
                if not items and not has_empty_catalog_notice(html):
                    errors.append(f"Catálogo não reconhecido em {page_url}.")
                    break
                new_items = [item for item in items if item.external_id not in seen_ids]
                if items and not new_items:
                    if next_url is not None and agency.catalog_form:
                        page_url = next_url
                        continue
                    errors.append(
                        f"Página sem novos imóveis em {page_url}; catálogo possivelmente truncado."
                    )
                    break
                collected.extend(new_items)
                seen_ids.update(item.external_id for item in new_items)
                if unresolved:
                    errors.append(f"Carregamento adicional por JavaScript não resolvido em {page_url}.")
                page_url = next_url
            else:
                if page_url is not None:
                    errors.append(f"Limite de {max_pages} páginas atingido em {entry_url}.")
        return ScrapeBatchResult(
            items=tuple(collected),
            requests_made=requests,
            had_section_errors=bool(errors),
            error_summary="; ".join(errors) if errors else None,
        )

    async def fetch_property_detail(self, agency: AgencyConfig, source_url: str) -> PropertyDetailPatch:
        """Fetch descriptions, fees, features, and photos only when enrichment is requested."""
        status, _, html = await self._http.get_text(source_url, agency_slug=agency.slug)
        if status >= 400:
            raise ScraperTransportError(agency.slug, source_url, f"HTTP {status}")
        return parse_generic_detail_html(html, agency.base_url)

"""Agil's public paginated search API, used by Fortun Imóveis."""

from __future__ import annotations

import re
from urllib.parse import quote, urlparse
from uuid import UUID

from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from imob.domain.enums import ListingPurpose
from imob.domain.models import AgencyConfig, PropertyDetailPatch, RawScrapedItem
from imob.errors import ScraperParseError, ScraperTransportError
from imob.ports import ScrapeBatchResult
from imob.scrapers.http_client import PoliteHttpClient
from imob.scrapers.parsing import parse_area_m2, parse_brl

_ROUTES = {"sale": "venda", "rent": "aluguel", "season": "temporada"}


class _Payload(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)


class _Area(_Payload):
    total: float | str | None = None
    total_unit: str = Field(default="m²", alias="totalUnityType")
    private: float | str | None = Field(default=None, alias="particular")
    private_unit: str = Field(default="m²", alias="particularUnityType")
    built: float | str | None = Field(default=None, alias="builtedArea")
    built_unit: str = Field(default="m²", alias="builtedAreaUnityType")


class _Seo(_Payload):
    title: str | None = None
    description: str | None = None
    url: str | None = None


class _Item(_Payload):
    uuid: UUID
    code: int = Field(ge=1)
    reference: str | None = None
    status: str
    goal: str
    category: str
    city: str
    neighborhood: str = ""
    price: float | None = None
    img: str | None = None
    bedrooms: int | None = Field(default=None, alias="amountBedroom", ge=0)
    suites: int | None = Field(default=None, alias="amountSuite", ge=0)
    bathrooms: int | None = Field(default=None, alias="amountBathroom", ge=0)
    garages: int | None = Field(default=None, alias="amountGarage", ge=0)
    area: _Area = Field(default_factory=_Area)
    seo: _Seo = Field(default_factory=_Seo)


class _Pagination(_Payload):
    step: int = Field(default=24, ge=1)
    last: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)
    total_items: int = Field(default=0, alias="totalItems", ge=0)


class _Search(_Payload):
    items: tuple[_Item, ...]
    pagination: _Pagination


class _Info(_Payload):
    description: str | None = None


class _Controls(_Payload):
    protected: bool = Field(alias="isProtected")


class _Identity(_Payload):
    uuid: UUID


class _Fees(_Payload):
    condo: float | None = Field(default=None, alias="condoFees")
    tax: float | None = None


class _Counts(_Payload):
    bedrooms: int | None = None
    suites: int | None = None
    bathrooms: int | None = None
    garages: int | None = None


class _Photo(_Payload):
    name: str


class _Detail(_Payload):
    controls: _Controls
    id: _Identity
    info: _Info
    pricing: _Fees
    amounts: _Counts
    area: _Area
    gallery: tuple[_Photo, ...] = ()


def _sqm(value: float | str | None, unit: str) -> float | None:
    return parse_area_m2(value) if unit.lower().replace(" ", "") in {"m²", "m2"} else None


def _photo_url(company: str, property_id: UUID, filename: str) -> str:
    return (
        f"https://new-agil-cdn.agil.net/cdn/agil/{company}/property/{property_id}"
        f"/medium/{quote(filename, safe='')}"
    )


def _listing(item: _Item, agency: AgencyConfig, company: str) -> RawScrapedItem:
    route = _ROUTES[item.goal]
    source_url = f"{agency.base_url}/imovel/{item.code}/{route}"
    if item.seo.url:
        source_url += "/" + quote(item.seo.url, safe="-")
    return RawScrapedItem(
        agency_slug=agency.slug,
        external_id=str(item.code),
        ref_code=item.reference or str(item.code),
        source_url=source_url,
        is_available=item.status == "active",
        title=item.seo.title or f"{item.category} em {item.city} - {item.neighborhood}",
        description=item.seo.description,
        raw_type=item.category,
        raw_city=item.city,
        raw_neighborhood=item.neighborhood,
        raw_purpose=ListingPurpose.SALE.value if item.goal == "sale" else ListingPurpose.RENT.value,
        sale_price=parse_brl(item.price) if item.goal == "sale" else None,
        rent_price=parse_brl(item.price) if item.goal != "sale" else None,
        area_total_m2=_sqm(item.area.total, item.area.total_unit),
        area_private_m2=_sqm(item.area.private, item.area.private_unit),
        area_built_m2=_sqm(item.area.built, item.area.built_unit),
        bedrooms=item.bedrooms,
        suites=item.suites,
        bathrooms=item.bathrooms,
        parking_spots=item.garages,
        image_urls=(_photo_url(company, item.uuid, item.img),) if item.img else (),
    )


class AgilScraper:
    """Query the same anonymous catalog endpoint and cursor used by the agency's public site."""

    def __init__(self, http_client: PoliteHttpClient) -> None:
        """Share the application's rate-limited client."""
        self._http = http_client

    async def fetch_catalog(self, agency: AgencyConfig, *, max_pages: int) -> ScrapeBatchResult:
        """Read sale, annual rental, and seasonal rental pages, including records without prices."""
        endpoint = agency.entry_urls[0][1]
        company = endpoint.rstrip("/").split("/")[-2]
        collected: list[RawScrapedItem] = []
        errors: list[str] = []
        requests = 0
        for goal in _ROUTES:
            cursor = _Pagination()
            seen: set[int] = set()
            for _ in range(max_pages):
                requests += 1
                try:
                    payload = await self._http.post_json(
                        endpoint,
                        agency_slug=agency.slug,
                        json_body={
                            "mode": "default",
                            "sort": {"type": "approximation", "direction": None},
                            "params": {"goal": goal},
                            "pagination": cursor.model_dump(by_alias=True),
                        },
                    )
                    page = _Search.model_validate(payload)
                    if any(item.goal not in _ROUTES for item in page.items):
                        raise ScraperParseError(agency.slug, "Finalidade Agil não reconhecida.")
                except (ScraperTransportError, ScraperParseError, ValidationError) as exc:
                    errors.append(f"{goal}: {type(exc).__name__}: falha ao ler o catálogo Agil.")
                    break
                new_items = [item for item in page.items if item.code not in seen]
                if page.items and not new_items:
                    errors.append(f"{goal}: a API repetiu a página de imóveis.")
                    break
                collected.extend(_listing(item, agency, company) for item in new_items)
                seen.update(item.code for item in new_items)
                if not page.items and page.pagination.total_items:
                    errors.append(f"{goal}: a API omitiu imóveis anunciados na contagem.")
                    break
                if page.pagination.last >= page.pagination.total:
                    break
                if page.pagination.last <= cursor.last:
                    errors.append(f"{goal}: paginação Agil não avançou.")
                    break
                cursor = page.pagination
            else:
                errors.append(f"{goal}: limite de {max_pages} páginas atingido.")
        return ScrapeBatchResult(
            items=tuple(collected),
            requests_made=requests,
            had_section_errors=bool(errors),
            error_summary="; ".join(errors) if errors else None,
        )

    async def fetch_property_detail(self, agency: AgencyConfig, source_url: str) -> PropertyDetailPatch:
        """Read public details and honor the site's explicit identification requirement."""
        match = re.match(r"/imovel/(\d+)/(venda|aluguel|temporada)(?:/|$)", urlparse(source_url).path)
        if match is None:
            raise ScraperParseError(agency.slug, "URL de imóvel Agil não reconhecida.")
        goal = next(key for key, route in _ROUTES.items() if route == match.group(2))
        root = agency.entry_urls[0][1].removesuffix("/search")
        company = root.rstrip("/").split("/")[-1]
        status, _, text = await self._http.get_text(
            f"{root}/property/{match.group(1)}/{goal}?showHidden=false",
            agency_slug=agency.slug,
        )
        if status >= 400:
            raise ScraperTransportError(agency.slug, source_url, f"HTTP {status}")
        try:
            detail = _Detail.model_validate_json(text)
        except ValidationError as exc:
            raise ScraperParseError(agency.slug, "Detalhes Agil fora do formato esperado.") from exc
        if detail.controls.protected:
            raise ScraperParseError(
                agency.slug, "O site exige identificação para consultar estes detalhes."
            )
        description = (
            BeautifulSoup(detail.info.description or "", "lxml").get_text("\n", strip=True) or None
        )
        return PropertyDetailPatch(
            description=description,
            condo_fee=parse_brl(detail.pricing.condo),
            iptu_fee=parse_brl(detail.pricing.tax),
            area_total_m2=_sqm(detail.area.total, detail.area.total_unit),
            area_private_m2=_sqm(detail.area.private, detail.area.private_unit),
            area_built_m2=_sqm(detail.area.built, detail.area.built_unit),
            bedrooms=detail.amounts.bedrooms,
            suites=detail.amounts.suites,
            bathrooms=detail.amounts.bathrooms,
            parking_spots=detail.amounts.garages,
            image_urls=tuple(_photo_url(company, detail.id.uuid, photo.name) for photo in detail.gallery),
        )

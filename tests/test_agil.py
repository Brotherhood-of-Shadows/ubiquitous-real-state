"""Public Agil API contracts, pagination, price-less records, and protected enrichment."""

from __future__ import annotations

import json

import httpx
import pytest

from imob.domain.normalizer import normalize_scraped_item
from imob.domain.registry import AGENCIES_BY_SLUG
from imob.errors import ScraperParseError
from imob.scrapers.agil import AgilScraper
from imob.scrapers.http_client import PoliteHttpClient


def _item(code: int, goal: str, city: str, price: float | None) -> dict[str, object]:
    return {
        "uuid": f"00000000-0000-0000-0000-{code:012d}",
        "code": code,
        "goal": goal,
        "status": "active",
        "category": "Apartamento",
        "city": city,
        "neighborhood": "Centro",
        "price": price,
        "img": "property.webp",
        "amountBedroom": 3,
        "amountSuite": 1,
        "amountBathroom": 2,
        "amountGarage": 2,
        "area": {"total": 120, "totalUnityType": "m²", "particular": "80,50", "builtedArea": 90},
    }


@pytest.mark.asyncio
async def test_catalog_uses_native_cursor_and_preserves_prices_and_locations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read every transaction section, keep unknown prices, and retain explicit city evidence."""
    requested: list[tuple[str, int]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        goal = payload["params"]["goal"]
        last = payload["pagination"]["last"]
        assert payload["pagination"]["step"] == 24
        requested.append((goal, last))
        if goal == "sale":
            items = (
                [_item(1, goal, "Pato Branco", None), _item(2, goal, "Cascavel", 500000)]
                if last == 0
                else [_item(3, goal, "Pato Branco", 400000)]
            )
            page = {"step": 24, "last": last + 1, "total": 2, "totalItems": 3}
        else:
            items = [_item(4, goal, "Pato Branco", 2000)] if goal == "rent" else []
            page = {"step": 24, "last": 1, "total": 1 if items else 0, "totalItems": len(items)}
        return httpx.Response(200, json={"items": items, "pagination": page})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))

    def make_client(**kwargs: object) -> httpx.AsyncClient:
        return client

    monkeypatch.setattr(httpx, "AsyncClient", make_client)
    agency = AGENCIES_BY_SLUG["fortun"]
    async with PoliteHttpClient(per_domain_delay_seconds=0) as http:
        result = await AgilScraper(http).fetch_catalog(agency, max_pages=2)
    assert requested == [("sale", 0), ("sale", 1), ("rent", 0), ("season", 0)]
    assert (len(result.items), result.requests_made, result.had_section_errors) == (4, 4, False)
    normalized = [normalize_scraped_item(item, agency) for item in result.items]
    accepted = [item for item in normalized if item is not None]
    assert [item.external_id for item in accepted] == ["1", "3", "4"]
    assert accepted[0].price_on_request
    assert (accepted[0].area_total_m2, accepted[0].area_private_m2) == (120, 80.5)
    assert accepted[-1].rent_price == 2000
    assert result.items[0].image_urls[0].endswith("/medium/property.webp")


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [True, False])
async def test_invalid_or_stuck_pagination_is_partial(
    monkeypatch: pytest.MonkeyPatch, invalid: bool
) -> None:
    """An invalid schema or non-advancing cursor cannot authorize deactivation of old listings."""
    payload = (
        {"items": []}
        if invalid
        else {
            "items": [_item(1, "sale", "Pato Branco", None)],
            "pagination": {"step": 24, "last": 0, "total": 2, "totalItems": 2},
        }
    )
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    )

    def make_client(**kwargs: object) -> httpx.AsyncClient:
        return client

    monkeypatch.setattr(httpx, "AsyncClient", make_client)
    async with PoliteHttpClient(per_domain_delay_seconds=0) as http:
        result = await AgilScraper(http).fetch_catalog(AGENCIES_BY_SLUG["fortun"], max_pages=3)
    assert result.had_section_errors
    assert result.error_summary


@pytest.mark.asyncio
@pytest.mark.parametrize("protected", [True, False])
async def test_detail_honors_the_sites_identification_gate(
    monkeypatch: pytest.MonkeyPatch, protected: bool
) -> None:
    """Enrich only public details; never switch on hidden listings or bypass identification."""
    payload = {
        "controls": {"isProtected": protected},
        "id": {"uuid": "00000000-0000-0000-0000-000000000136"},
        "info": {"description": "<p>Casa com churrasqueira.</p>"},
        "pricing": {"condoFees": 300, "tax": 50},
        "amounts": {"bedrooms": 3, "suites": 1, "bathrooms": 2, "garages": 2},
        "area": {"total": 100, "totalUnityType": "m²", "particular": 80},
        "gallery": [{"name": "property.webp"}],
    }

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.params["showHidden"] == "false"
        assert request.url.path.endswith("/property/136/sale")
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))

    def make_client(**kwargs: object) -> httpx.AsyncClient:
        return client

    monkeypatch.setattr(httpx, "AsyncClient", make_client)
    agency = AGENCIES_BY_SLUG["fortun"]
    async with PoliteHttpClient(per_domain_delay_seconds=0) as http:
        scraper = AgilScraper(http)
        if protected:
            with pytest.raises(ScraperParseError, match="exige identificação"):
                await scraper.fetch_property_detail(agency, agency.base_url + "/imovel/136/venda")
        else:
            result = await scraper.fetch_property_detail(agency, agency.base_url + "/imovel/136/venda")
            assert (result.description, result.condo_fee, result.iptu_fee) == (
                "Casa com churrasqueira.",
                300,
                50,
            )
            assert result.area_total_m2 == 100
            assert len(result.image_urls) == 1

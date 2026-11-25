"""Opt-in public-site integration checks; run explicitly with pytest tests/live_catalogs.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from imob.domain.normalizer import normalize_scraped_item
from imob.domain.registry import AGENCIES_BY_SLUG
from imob.scrapers import PlatformScraperRegistry
from imob.scrapers.http_client import PoliteHttpClient
from imob.scrapers.parsing import has_empty_catalog_notice


class _RecordingClient(PoliteHttpClient):
    def __init__(self, directory: Path) -> None:
        super().__init__(timeout_seconds=15, max_retries=0)
        self.directory = directory
        self.responses = 0

    async def get_text(
        self,
        url: str,
        *,
        agency_slug: str,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        response = await super().get_text(url, agency_slug=agency_slug, params=params, headers=headers)
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / f"{self.responses}.html").write_text(response[2], encoding="utf-8")
        self.responses += 1
        return response

    async def post_text(
        self,
        url: str,
        *,
        agency_slug: str,
        form_data: dict[str, str],
    ) -> tuple[int, str, str]:
        response = await super().post_text(url, agency_slug=agency_slug, form_data=form_data)
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / f"{self.responses}.html").write_text(response[2], encoding="utf-8")
        self.responses += 1
        return response


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "slug",
    [
        "valen",
        "cristian",
        "familiar",
        "fortun",
        "alvaro",
        "carlos",
        "esatta",
        "tradicao",
        "cattuci",
        "veronese",
        "duo",
        "mattia",
        "america",
        "acacio",
        "imperador",
        "mazorana",
        "pauloreis",
        "negociar",
        "magcen",
        "morosini",
        "popiel",
        "cad",
        "mariely",
        "junior",
    ],
)
async def test_live_catalog(slug: str) -> None:
    """Exercise complete real catalogs within the application's page cap and save diagnostics."""
    agency = AGENCIES_BY_SLUG[slug]
    directory = Path("/tmp/imob-live-catalogs") / slug
    recorder = _RecordingClient(directory)
    async with recorder as client:
        result = (
            await PlatformScraperRegistry(client)
            .get_scraper(agency.platform)
            .fetch_catalog(agency, max_pages=60)
        )
    directory.mkdir(parents=True, exist_ok=True)
    accepted = [
        normalized
        for item in result.items
        if (normalized := normalize_scraped_item(item, agency)) is not None
    ]
    summary = {
        "agency": slug,
        "requests": result.requests_made,
        "raw_items": len(result.items),
        "accepted_city": len(accepted),
        "unique_accepted": len({item.external_id for item in accepted}),
        "partial": result.had_section_errors,
        "error": result.error_summary,
        "sample": [item.model_dump(mode="json") for item in result.items[:3]],
    }
    (directory / "result.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (directory / "listings.json").write_text(
        json.dumps([item.model_dump(mode="json") for item in result.items], ensure_ascii=False),
        encoding="utf-8",
    )
    assert not result.had_section_errors, result.error_summary
    if result.items:
        assert accepted, "No verifiable Pato Branco listings were accepted"
    else:
        assert any(
            has_empty_catalog_notice(path.read_text())
            for path in (directory / f"{i}.html" for i in range(recorder.responses))
        )

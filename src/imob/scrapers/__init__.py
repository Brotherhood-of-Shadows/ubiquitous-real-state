"""Wire supported HTML and API catalog engines."""

from __future__ import annotations

from imob.domain.enums import PlatformKind
from imob.ports import AgencyScraperProtocol
from imob.scrapers.agil import AgilScraper
from imob.scrapers.apolar import ApolarScraper
from imob.scrapers.http_client import PoliteHttpClient
from imob.scrapers.imonov import ImonovScraper
from imob.scrapers.moretti import MorettiScraper
from imob.scrapers.public_html import PublicHtmlScraper
from imob.scrapers.uniq import UniqScraper
from imob.scrapers.widesys import WidesysScraper


class PlatformScraperRegistry:
    """Maps `PlatformKind` identifiers to concrete async scraper adapters."""

    def __init__(self, http_client: PoliteHttpClient, *, target_city: str = "Pato Branco") -> None:
        """Initialize adapters sharing a single rate-limited `PoliteHttpClient`.

        Args:
            http_client: Shared polite HTTP client.
            target_city: City filter passed to bulk API adapters such as Apolar.
        """
        self._scrapers: dict[PlatformKind, AgencyScraperProtocol] = {
            PlatformKind.WIDESYS: WidesysScraper(http_client),
            PlatformKind.IMONOV: ImonovScraper(http_client),
            PlatformKind.UNIQ: UniqScraper(http_client),
            PlatformKind.MORETTI: MorettiScraper(http_client),
            PlatformKind.APOLAR: ApolarScraper(http_client, target_city=target_city),
            PlatformKind.PUBLIC_HTML: PublicHtmlScraper(http_client),
            PlatformKind.AGIL: AgilScraper(http_client),
        }

    def get_scraper(self, platform: PlatformKind) -> AgencyScraperProtocol:
        """Look up the scraper adapter for `platform`.

        Args:
            platform: Platform enum value.

        Returns:
            Registered `AgencyScraperProtocol` adapter.
        """
        return self._scrapers[platform]

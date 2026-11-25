"""Polite asynchronous HTTP client with per-domain rate limiting and bounded retries."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from imob.errors import ScraperTransportError

_DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) ImobPatoBrancoAggregator/0.1 (+local-personal-search)"),
    "Accept": "text/html,application/xhtml+xml,application/json,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.7",
}

_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 502, 503, 504})


def _decode_response_text(resp: httpx.Response) -> str:
    """Decode response body as UTF-8 first, falling back to CP1252 for legacy ASP pages."""
    encoding = resp.encoding or "utf-8"
    try:
        return resp.content.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        try:
            return resp.content.decode("utf-8")
        except UnicodeDecodeError:
            return resp.content.decode("cp1252", errors="replace")


class PoliteHttpClient:
    """Async HTTP client enforcing 1 concurrent request and polite delay per host."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 25.0,
        per_domain_delay_seconds: float = 0.45,
        max_retries: int = 2,
    ) -> None:
        """Initialize the rate-limited HTTP client.

        Args:
            timeout_seconds: Per-request network timeout in seconds.
            per_domain_delay_seconds: Minimum pause between sequential requests to the same host.
            max_retries: Maximum retry attempts on transient 5xx/timeout errors.
        """
        self._timeout = httpx.Timeout(timeout_seconds, connect=12.0)
        self._delay = per_domain_delay_seconds
        self._max_retries = max_retries
        self._client: httpx.AsyncClient | None = None
        self._domain_locks: dict[str, asyncio.Lock] = {}
        self._last_request_ts: dict[str, float] = {}

    async def __aenter__(self) -> PoliteHttpClient:
        """Open the underlying `httpx.AsyncClient` connection pool."""
        self._client = httpx.AsyncClient(
            headers=_DEFAULT_HEADERS,
            timeout=self._timeout,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=30, max_keepalive_connections=15),
        )
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        """Close the underlying `httpx.AsyncClient` cleanly."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _lock_for_url(self, url: str) -> tuple[str, asyncio.Lock]:
        host = urlparse(url).netloc.lower()
        lock = self._domain_locks.get(host)
        if lock is None:
            lock = asyncio.Lock()
            self._domain_locks[host] = lock
        return host, lock

    async def get_text(
        self,
        url: str,
        *,
        agency_slug: str,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        """Perform a polite rate-limited HTTP GET returning status, final URL, and body text.

        Args:
            url: Target URL.
            agency_slug: Agency identifier for error diagnostics.
            params: Optional query parameters.
            headers: Optional extra request headers.

        Returns:
            Tuple `(status_code, final_url, decoded_text)`.

        Raises:
            ScraperTransportError: On network failure, anti-bot gate, or unexpected HTTP error.
        """
        resp = await self._request("GET", url, agency_slug=agency_slug, params=params, headers=headers)
        return resp.status_code, str(resp.url), _decode_response_text(resp)

    async def post_text(
        self,
        url: str,
        *,
        agency_slug: str,
        form_data: dict[str, str],
    ) -> tuple[int, str, str]:
        """Submit a public catalog search form and return status, final URL, and HTML.

        Raises:
            ScraperTransportError: On a transport failure or anti-bot challenge.
        """
        resp = await self._request("POST", url, agency_slug=agency_slug, form_data=form_data)
        return resp.status_code, str(resp.url), _decode_response_text(resp)

    async def post_json(
        self,
        url: str,
        *,
        agency_slug: str,
        json_body: dict[str, Any] | None = None,
        form_data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Perform a polite rate-limited HTTP POST and decode JSON object response.

        Args:
            url: Target endpoint URL.
            agency_slug: Agency identifier for error diagnostics.
            json_body: Optional JSON request payload.
            form_data: Optional form-encoded request payload.
            headers: Optional extra request headers.

        Returns:
            Decoded JSON dictionary.

        Raises:
            ScraperTransportError: On network error, non-2xx status, or invalid JSON.
        """
        resp = await self._request(
            "POST",
            url,
            agency_slug=agency_slug,
            json_body=json_body,
            form_data=form_data,
            headers=headers,
        )
        if resp.status_code >= 400:
            raise ScraperTransportError(agency_slug, url, f"HTTP {resp.status_code}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise ScraperTransportError(agency_slug, url, f"Invalid JSON response: {exc}") from exc
        if not isinstance(payload, dict):
            raise ScraperTransportError(agency_slug, url, "Expected JSON object response")
        return payload

    async def _request(
        self,
        method: str,
        url: str,
        *,
        agency_slug: str,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        form_data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        if self._client is None:
            raise ScraperTransportError(agency_slug, url, "PoliteHttpClient is not open")

        host, lock = self._lock_for_url(url)
        last_exc: Exception | None = None

        for attempt in range(self._max_retries + 1):
            async with lock:
                elapsed = time.monotonic() - self._last_request_ts.get(host, 0.0)
                if elapsed < self._delay:
                    await asyncio.sleep(self._delay - elapsed)
                try:
                    resp = await self._client.request(
                        method,
                        url,
                        params=params,
                        json=json_body,
                        data=form_data,
                        headers=headers,
                    )
                    self._last_request_ts[host] = time.monotonic()
                except httpx.HTTPError as exc:
                    self._last_request_ts[host] = time.monotonic()
                    last_exc = exc
                    if attempt < self._max_retries:
                        await asyncio.sleep(0.8 * (attempt + 1))
                        continue
                    raise ScraperTransportError(agency_slug, url, f"{type(exc).__name__}: {exc}") from exc

            # Never bypass anti-bot challenges; report cleanly if gated
            if resp.status_code in (403, 503):
                snippet = resp.text[:4000].lower()
                if "cf-challenge" in snippet or "turnstile" in snippet or "verify you are human" in snippet:
                    raise ScraperTransportError(
                        agency_slug,
                        url,
                        f"Blocked by anti-bot challenge (HTTP {resp.status_code})",
                    )

            if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < self._max_retries:
                retry_after = resp.headers.get("Retry-After")
                wait_s = (
                    float(retry_after) if retry_after and retry_after.isdigit() else 1.0 * (attempt + 1)
                )
                await asyncio.sleep(min(wait_s, 10.0))
                continue

            return resp

        raise ScraperTransportError(agency_slug, url, str(last_exc or "Exhausted retries"))

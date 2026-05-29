"""Thin HTTP client wrapper for KBO data scraping.

Provides retry logic, a custom User-Agent, timeout enforcement, a maximum
response-size cap so that runaway payloads are rejected early, per-call
headers (e.g. Referer for Naver), POST support (for the KBO Official backup
endpoint), and a per-host rate limiter for polite crawling.

Usage::

    with HttpClient() as http:
        result = http.fetch("https://example.com/page")
        print(result.body[:200])
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from urllib.parse import urlsplit

import httpx

__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_RETRIES",
    "DEFAULT_TIMEOUT",
    "RETRY_BACKOFF_SECONDS",
    "USER_AGENT",
    "FetchError",
    "FetchResult",
    "HttpClient",
]

USER_AGENT: Final = "kbo-lineup-lab/0.1 (+https://github.com/songyoungin/kbo-lineup-lab)"
DEFAULT_TIMEOUT: Final = 15.0  # seconds
DEFAULT_MAX_BYTES: Final = 5 * 1024 * 1024  # 5 MiB cap on response body
DEFAULT_RETRIES: Final = 3
RETRY_BACKOFF_SECONDS: Final[tuple[float, ...]] = (1.0, 2.0, 4.0)  # backoff per attempt


@dataclass(frozen=True)
class FetchResult:
    """Successful response from a single HTTP request."""

    url: str
    status_code: int
    content_type: str
    body: str
    fetched_at: datetime


class FetchError(Exception):
    """Raised when a request fails after retries or violates a safety limit."""


class HttpClient:
    """Thin wrapper around httpx with retry, timeout, UA, and size limits.

    Intended for KBO data scraping; behaves politely (sequential, low concurrency).
    All configuration parameters are injectable so tests can override them without
    touching global state or patching module-level attributes.

    Args:
        timeout: Per-request timeout in seconds.
        max_bytes: Maximum allowed response body size in bytes. Responses that
            exceed this are rejected with FetchError even when the HTTP status
            is 200.
        max_retries: Number of request attempts before giving up.
        user_agent: Value for the ``User-Agent`` request header.
        retry_backoff: Sleep durations (in seconds) between retry attempts.
            Must contain at least ``max_retries - 1`` entries. Pass
            ``(0.0,) * max_retries`` in tests to skip actual sleeping.
        client: Optional pre-built ``httpx.Client``. Inject a client backed by
            ``httpx.MockTransport`` in tests to avoid network access. **When
            ``client`` is provided, ``user_agent`` and ``timeout`` are
            ignored** — requests use the injected client's own headers and
            timeout.
        min_interval: Minimum interval (in seconds) between successive requests
            to the same host. ``0.0`` (default) disables throttling.
    """

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_retries: int = DEFAULT_RETRIES,
        user_agent: str = USER_AGENT,
        retry_backoff: tuple[float, ...] = RETRY_BACKOFF_SECONDS,
        client: httpx.Client | None = None,
        min_interval: float = 0.0,
    ) -> None:
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._max_retries = max_retries
        self._user_agent = user_agent
        self._retry_backoff = retry_backoff
        self._client = client or httpx.Client(timeout=timeout, headers={"User-Agent": user_agent})
        self._min_interval = min_interval
        self._last_request_at: dict[str, float] = {}

    def _throttle(self, url: str) -> None:
        """Sleep so successive requests to the same host honor min_interval.

        Args:
            url: The request URL (used to extract the host).
        """
        if self._min_interval <= 0:
            return
        host = urlsplit(url).netloc
        last = self._last_request_at.get(host)
        now = time.monotonic()
        if last is not None:
            wait = self._min_interval - (now - last)
            if wait > 0:
                time.sleep(wait)
        self._last_request_at[host] = time.monotonic()

    def _request(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, str] | None,
        headers: dict[str, str] | None,
    ) -> FetchResult:
        """Issue an HTTP request with retries, size limit, and rate limiting.

        Args:
            method: HTTP method string (``"GET"`` or ``"POST"``).
            url: Fully qualified request URL.
            data: Form data to POST. ``None`` for GET.
            headers: Extra request headers. ``None`` to add nothing.

        Returns:
            A :class:`FetchResult` with the response body and metadata.

        Raises:
            FetchError: If all retry attempts fail, or if the response body
                exceeds max_bytes.
        """
        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                # Throttling per attempt is intentional: each retry is also a real
                # request to the same host, so it must honor the min_interval spacing.
                self._throttle(url)
                if method == "POST":
                    response = self._client.post(url, data=data, headers=headers)
                else:
                    response = self._client.get(url, headers=headers)
                response.raise_for_status()
                if len(response.content) > self._max_bytes:
                    raise FetchError(
                        f"Response too large: {len(response.content)} > {self._max_bytes}"
                    )
                return FetchResult(
                    url=url,
                    status_code=response.status_code,
                    content_type=response.headers.get("content-type", "application/octet-stream"),
                    body=response.text,
                    fetched_at=datetime.now(UTC),
                )
            except FetchError:
                # Size-limit violations are not transient — propagate immediately.
                raise
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                last_error = exc
                if attempt + 1 < self._max_retries:
                    backoff = (
                        self._retry_backoff[attempt]
                        if attempt < len(self._retry_backoff)
                        else self._retry_backoff[-1]
                    )
                    time.sleep(backoff)
        raise FetchError(f"Failed after {self._max_retries} attempts: {last_error}") from last_error

    def fetch(self, url: str, *, headers: dict[str, str] | None = None) -> FetchResult:
        """Synchronously GET *url* with retries.

        Args:
            url: Fully qualified URL to GET.
            headers: Optional extra request headers (e.g. ``{"Referer": "..."}``).

        Returns:
            A :class:`FetchResult` with the response body and metadata.

        Raises:
            FetchError: If all retry attempts fail, or if the response body
                exceeds max_bytes.
        """
        return self._request("GET", url, data=None, headers=headers)

    def post(
        self,
        url: str,
        *,
        data: dict[str, str],
        headers: dict[str, str] | None = None,
    ) -> FetchResult:
        """POST form-encoded *data* to *url* with retries.

        Args:
            url: Fully qualified URL to POST.
            data: Form fields (``application/x-www-form-urlencoded``).
            headers: Optional extra request headers (e.g. ``{"Referer": "..."}``).

        Returns:
            A :class:`FetchResult` with the response body and metadata.

        Raises:
            FetchError: If all retry attempts fail, or if the response body
                exceeds max_bytes.
        """
        return self._request("POST", url, data=data, headers=headers)

    def close(self) -> None:
        """Close the underlying httpx client and release connections."""
        self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

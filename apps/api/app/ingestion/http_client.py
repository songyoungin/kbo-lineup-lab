"""Thin HTTP client wrapper for KBO data scraping.

Provides retry logic, a custom User-Agent, timeout enforcement, and a maximum
response-size cap so that runaway payloads are rejected early.

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
    """Successful response from a single HTTP GET."""

    url: str
    status_code: int
    content_type: str
    body: str
    fetched_at: datetime


class FetchError(Exception):
    """Raised when a fetch fails after retries or violates a safety limit."""


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
        max_retries: Number of fetch attempts before giving up.
        user_agent: Value for the ``User-Agent`` request header.
        retry_backoff: Sleep durations (in seconds) between retry attempts.
            Must contain at least ``max_retries - 1`` entries. Pass
            ``(0.0,) * max_retries`` in tests to skip actual sleeping.
        client: Optional pre-built ``httpx.Client``. Inject a client backed by
            ``httpx.MockTransport`` in tests to avoid network access. **When
            ``client`` is provided, ``user_agent`` and ``timeout`` are
            ignored** — fetches use the injected client's own headers and
            timeout. Configure those on the client itself before passing it
            in (e.g. ``httpx.Client(timeout=..., headers={"User-Agent": ...})``).
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
    ) -> None:
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._max_retries = max_retries
        self._user_agent = user_agent
        self._retry_backoff = retry_backoff
        self._client = client or httpx.Client(timeout=timeout, headers={"User-Agent": user_agent})

    def fetch(self, url: str) -> FetchResult:
        """Synchronously fetch *url* with retries on transient errors.

        Args:
            url: Fully qualified URL to GET.

        Returns:
            A :class:`FetchResult` with the response body and metadata.

        Raises:
            FetchError: If all retry attempts fail, or if the response body
                exceeds :attr:`max_bytes`.
        """
        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                response = self._client.get(url)
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

    def close(self) -> None:
        """Close the underlying httpx client and release connections."""
        self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

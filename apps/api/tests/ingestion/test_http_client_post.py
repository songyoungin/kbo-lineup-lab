"""Verifies HttpClient.post (form-encoded), per-call headers, and the per-host
rate limiter. No real network — httpx.MockTransport only."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from app.ingestion.http_client import HttpClient

MockHttpBuilder = Callable[[Callable[[httpx.Request], httpx.Response]], HttpClient]


def test_post_sends_form_body_and_returns_result(mock_http: MockHttpBuilder) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["body"] = request.content.decode()
        seen["referer"] = request.headers.get("Referer")
        return httpx.Response(200, json={"code": 100, "game": []})

    http = mock_http(handler)
    result = http.post(
        "https://www.koreabaseball.com/ws/Main.asmx/GetKboGameList",
        data={"leId": "1", "srId": "0", "date": "20250514"},
        headers={"Referer": "https://www.koreabaseball.com/Schedule/Schedule.aspx"},
    )
    assert seen["method"] == "POST"
    body = str(seen["body"])
    assert "leId=1" in body and "date=20250514" in body
    assert seen["referer"] == "https://www.koreabaseball.com/Schedule/Schedule.aspx"
    assert result.status_code == 200
    assert json.loads(str(result.body))["code"] == 100


def test_fetch_passes_per_call_headers(mock_http: MockHttpBuilder) -> None:
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["referer"] = request.headers.get("Referer")
        return httpx.Response(200, text="ok")

    http = mock_http(handler)
    http.fetch(
        "https://api-gw.sports.naver.com/x", headers={"Referer": "https://m.sports.naver.com/"}
    )
    assert seen["referer"] == "https://m.sports.naver.com/"


def test_rate_limiter_enforces_min_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("app.ingestion.http_client.time.sleep", lambda s: sleeps.append(s))
    clock = {"t": 1000.0}
    monkeypatch.setattr("app.ingestion.http_client.time.monotonic", lambda: clock["t"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    # The mock_http fixture can't set min_interval, so build the HttpClient directly here.
    http = HttpClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        retry_backoff=(0.0,),
        min_interval=5.0,
    )
    http.fetch("https://api-gw.sports.naver.com/a")  # first call: no wait
    http.fetch("https://api-gw.sports.naver.com/b")  # immediate second: must wait ~5s
    assert any(s >= 4.9 for s in sleeps), f"expected a ~5s throttle sleep, got {sleeps}"

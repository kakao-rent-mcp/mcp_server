"""odcloud 클라이언트 재시도 동작 테스트."""

from __future__ import annotations

import httpx
import pytest
import respx

from slug_mcp.clients import odcloud

_URL = "https://api.odcloud.kr/api/Svc/v1/op"


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch):
    monkeypatch.setenv("DECODING_KEY", "test-key")
    monkeypatch.setattr(odcloud, "_RETRY_BACKOFF_SECONDS", (0.0, 0.0))


@respx.mock
async def test_retries_transient_5xx_then_succeeds():
    route = respx.get(_URL).mock(
        side_effect=[httpx.Response(500), httpx.Response(200, json={"data": [1]})]
    )
    result = await odcloud.get("Svc", "op")
    assert result == {"data": [1]}
    assert route.call_count == 2  # 최초 실패 후 재시도로 성공


@respx.mock
async def test_does_not_retry_client_error():
    route = respx.get(_URL).mock(return_value=httpx.Response(401))
    with pytest.raises(httpx.HTTPStatusError):
        await odcloud.get("Svc", "op")
    assert route.call_count == 1  # 4xx는 재시도하지 않음


@respx.mock
async def test_gives_up_after_retries():
    route = respx.get(_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        await odcloud.get("Svc", "op")
    assert route.call_count == 3  # 최초 + 재시도 2회

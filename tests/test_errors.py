"""외부 API 조회 도구의 에러 정제(refine_errors) 검증.

원본 예외를 그대로 노출하지 않고 {status:"error", message}로 정제해 돌려준다.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from slug_mcp.clients import odcloud
from slug_mcp.tools import competition, lh_lease, notices

_SEARCH_URL = "https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/getAPTLttotPblancDetail"
_CMPET_URL = "https://api.odcloud.kr/api/ApplyhomeInfoCmpetRtSvc/v1/getAPTLttotPblancCmpet"


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch):
    # 5xx 재시도 대기시간을 없애 테스트를 빠르게 유지한다.
    monkeypatch.setattr(odcloud, "_RETRY_BACKOFF_SECONDS", (0.0, 0.0))


@respx.mock
async def test_server_error_is_refined():
    """공공데이터 서버 5xx는 원본 예외 대신 안내 메시지로 정제된다."""
    respx.get(_SEARCH_URL).mock(return_value=httpx.Response(503))

    result = await notices.search_housing_notices(region="경기")

    assert result["status"] == "error"
    assert "다시 시도" in result["message"]
    assert "503" not in result["message"]  # 원본 상태코드 노출 안 함


@respx.mock
async def test_bad_request_is_refined():
    """4xx(잘못된 요청)도 정제된 안내로 바뀐다."""
    respx.get(_CMPET_URL).mock(return_value=httpx.Response(400))

    result = await competition.get_competition_stats("bad-no")

    assert result["status"] == "error"
    assert "입력값" in result["message"]


_MDL_URL = "https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/getAPTLttotPblancMdl"


async def test_network_error_is_refined():
    """네트워크 오류(연결 실패)도 정제된다. (get_notice_detail은 두 엔드포인트 동시 호출)"""
    with respx.mock:
        respx.get(_SEARCH_URL).mock(side_effect=httpx.ConnectError("boom"))
        respx.get(_MDL_URL).mock(side_effect=httpx.ConnectError("boom"))
        result = await notices.get_notice_detail("2026000320")

    assert result["status"] == "error"
    assert "연결" in result["message"]


async def test_invalid_region_message_passthrough():
    """지역명 검증 실패(ValueError)는 원인 안내 메시지를 그대로 전달한다."""
    result = await lh_lease.search_lease_notices(
        start_date="20200101", end_date="20200201", region="없는지역"
    )

    assert result["status"] == "error"
    assert "알 수 없는 지역" in result["message"]

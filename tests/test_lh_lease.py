from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from slug_mcp.models import LhNoticeType
from slug_mcp.tools import lh_lease

FIXTURES = Path(__file__).parent / "fixtures"

_LIST_URL = "https://apis.data.go.kr/B552555/lhLeaseNoticeInfo1/lhLeaseNoticeInfo1"


def _load(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@respx.mock
async def test_search_lease_notices_sends_params_and_parses_header():
    route = respx.get(_LIST_URL).mock(
        return_value=httpx.Response(200, json=_load("lh_lease_notice_list.json"))
    )

    result = await lh_lease.search_lease_notices(
        start_date="20200308",
        end_date="20200508",
        notice_type=LhNoticeType.SALE_HOUSE,
        region="경기",
        per_page=10,
    )

    assert route.called
    params = route.calls[0].request.url.params
    assert params["PAN_ST_DT"] == "20200308"
    assert params["PAN_ED_DT"] == "20200508"
    assert params["UPP_AIS_TP_CD"] == "05"  # notice_type enum -> code
    assert params["CNP_CD"] == "41"  # region name -> LH region code

    # 청약홈 검색 도구와 동일한 정제 반환형({total, count, notices})
    assert result["total"] == 2
    assert result["count"] == 2
    assert result["notices"][0]["id"] == "0000059187"
    assert result["notices"][0]["name"] == "고양삼송 공공분양주택"
    assert result["notices"][0]["type"] == "분양주택"
    # 원본 코드필드는 정제되어 노출되지 않는다.
    assert "PAN_ID" not in result["notices"][0]
    # 상세조회 도구 제거로 LH-내부 체이닝 코드필드도 더 이상 노출하지 않는다.
    for dead in ("supply_info_type", "upper_type_code", "system_div_code", "detail_type_code"):
        assert dead not in result["notices"][0]
    # 공고문·상세는 detail_url로 안내하므로 링크는 남긴다.
    assert result["notices"][0]["detail_url"].startswith("http")


def test_notice_type_accepts_korean_labels():
    # 클라이언트 AI가 설명의 한글 라벨을 그대로 넘겨도 해당 enum으로 수용한다(_missing_).
    assert LhNoticeType("분양주택") is LhNoticeType.SALE_HOUSE
    assert LhNoticeType("임대주택") is LhNoticeType.LEASE_HOUSE
    assert LhNoticeType(" 주거복지 ") is LhNoticeType.HOUSING_WELFARE  # 공백도 허용
    # 정규 영문 슬러그는 그대로 통과한다.
    assert LhNoticeType("sale_house") is LhNoticeType.SALE_HOUSE
    # 매핑에 없는 값은 여전히 검증 실패(오탐 방지).
    with pytest.raises(ValueError):
        LhNoticeType("없는유형")

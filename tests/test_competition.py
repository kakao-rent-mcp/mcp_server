from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx

from slug_mcp.tools import competition

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@respx.mock
async def test_get_competition_stats():
    respx.get("https://api.odcloud.kr/api/ApplyhomeInfoCmpetRtSvc/v1/getAPTLttotPblancCmpet").mock(
        return_value=httpx.Response(200, json=_load_fixture("apt_lttot_pblanc_cmpet.json"))
    )
    # getAptLttotPblancScore는 아직 실응답을 확보하지 못해, 빈 데이터 껍데기로만 검증한다.
    respx.get("https://api.odcloud.kr/api/ApplyhomeInfoCmpetRtSvc/v1/getAptLttotPblancScore").mock(
        return_value=httpx.Response(200, json=_load_fixture("apt_lttot_pblanc_score_empty.json"))
    )
    respx.get("https://api.odcloud.kr/api/ApplyhomeInfoCmpetRtSvc/v1/getAPTSpsplyReqstStus").mock(
        return_value=httpx.Response(200, json=_load_fixture("apt_spsply_reqst_stus.json"))
    )

    result = await competition.get_competition_stats("2026000273")

    assert result["competition"][0]["competition_rate"] == "3.79"
    assert result["competition"][0]["residence"] == "해당지역"
    assert "CMPET_RATE" not in result["competition"][0]  # 원본 코드필드 미노출
    # 특별공급(special_supply)은 후속 배치까지 원본 유지 — raw 필드가 그대로 있어야 정상.
    assert result["special_supply"][0]["SPSPLY_HSHLDCO"] == 10
    assert result["winning_scores"] == []


@respx.mock
async def test_get_winning_scores():
    """당첨가점 조회는 주택형별 최저·평균·최고 가점 행을 그대로 돌려준다."""
    respx.get("https://api.odcloud.kr/api/ApplyhomeInfoCmpetRtSvc/v1/getAptLttotPblancScore").mock(
        return_value=httpx.Response(200, json=_load_fixture("apt_lttot_pblanc_score.json"))
    )

    rows = await competition.get_winning_scores("2026000281")

    assert len(rows) == 4
    assert rows[0]["LWET_SCORE"] == "60"
    assert rows[0]["RESIDE_SENM"] == "해당지역"

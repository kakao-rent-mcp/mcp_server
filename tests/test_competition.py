from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx

from kakao_rent_mcp.tools import competition

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

    assert result["competition"][0]["CMPET_RATE"] == "3.79"
    assert result["special_supply"][0]["SPSPLY_HSHLDCO"] == 10
    assert result["winning_scores"] == []

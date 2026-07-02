from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx

from kr_housing_mcp.tools import notices

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@respx.mock
async def test_search_housing_notices_apt():
    fixture = _load_fixture("apt_lttot_pblanc_detail.json")
    route = respx.get(
        "https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/getAPTLttotPblancDetail"
    ).mock(return_value=httpx.Response(200, json=fixture))

    result = await notices.search_housing_notices(region="경기", per_page=3)

    assert route.called
    called_params = route.calls[0].request.url.params
    assert called_params["cond[SUBSCRPT_AREA_CODE_NM::EQ]"] == "경기"
    assert result["data"][0]["HOUSE_NM"] == "고양창릉 S-4블록 공공분양주택(본청약)"


@respx.mock
async def test_get_notice_detail_combines_detail_and_unit_types():
    respx.get("https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/getAPTLttotPblancDetail").mock(
        return_value=httpx.Response(200, json=_load_fixture("apt_lttot_pblanc_detail.json"))
    )
    respx.get("https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/getAPTLttotPblancMdl").mock(
        return_value=httpx.Response(200, json=_load_fixture("apt_lttot_pblanc_mdl.json"))
    )

    result = await notices.get_notice_detail("2026000320")

    assert result["notice"][0]["HOUSE_MANAGE_NO"] == "2026000320"
    assert result["unit_types"][0]["LTTOT_TOP_AMOUNT"] == "50724"

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx

from kakao_rent_mcp import models
from kakao_rent_mcp.tools import recommend

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _profile() -> models.UserProfile:
    return models.UserProfile(
        household_size=3,
        annual_household_income_10k_won=5000,
        has_no_house=True,
        marital_status=models.MaritalStatus.NEWLYWED,
        region="경기",
        subscription_account=models.SubscriptionAccount(
            account_type=models.SubscriptionAccountType.PUBLIC,
            joined_months_ago=24,
            payment_count=24,
        ),
    )


@respx.mock
async def test_recommend_housing_keeps_manual_review_candidates():
    """소득기준표 미설정으로 passed=False라도 needs_manual_review면 추천에서 빠지지 않아야 한다."""
    respx.get("https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/getAPTLttotPblancDetail").mock(
        return_value=httpx.Response(200, json=_load_fixture("apt_lttot_pblanc_detail.json"))
    )
    respx.get("https://api.odcloud.kr/api/ApplyhomeInfoCmpetRtSvc/v1/getAPTLttotPblancCmpet").mock(
        return_value=httpx.Response(200, json=_load_fixture("apt_lttot_pblanc_cmpet.json"))
    )
    respx.get("https://api.odcloud.kr/api/ApplyhomeInfoCmpetRtSvc/v1/getAptLttotPblancScore").mock(
        return_value=httpx.Response(200, json=_load_fixture("apt_lttot_pblanc_score_empty.json"))
    )
    respx.get("https://api.odcloud.kr/api/ApplyhomeInfoCmpetRtSvc/v1/getAPTSpsplyReqstStus").mock(
        return_value=httpx.Response(200, json=_load_fixture("apt_spsply_reqst_stus.json"))
    )

    result = await recommend.recommend_housing(_profile(), max_candidates_to_scan=5, top_n=3)

    assert result["total_candidates_scanned"] == 1
    assert result["eligible_or_review_needed_count"] == 1
    top = result["recommendations"][0]
    assert top["notice"]["HOUSE_MANAGE_NO"] == "2026000320"
    assert top["eligibility"]["needs_manual_review"] is True

"""recommend_housing v2 테스트.

세션 프로필 → 룰 엔진 분석 → 실시간 공고·경쟁률 결합 → 실현가능성 순 추천.
공고의 국민/민영 구분(HOUSE_DTL_SECD_NM)별로 자격 없는 트랙은 걸러져야 한다
(스펙 §8에서 지적한 '공고별 유형을 판정에 반영하지 않는 갭'의 해소).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from slug_mcp import store as store_module
from slug_mcp.store import ProfileStore
from slug_mcp.tools import recommend

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _fresh_store(monkeypatch):
    monkeypatch.setattr(store_module, "default_store", ProfileStore())


@pytest.fixture(autouse=True)
def _frozen_today(monkeypatch):
    # 공고 접수일 기준 '오늘'을 고정해, 시간이 지나도 마감 판정이 흔들리지 않게 한다.
    monkeypatch.setattr(recommend, "_today_kst", lambda: "2026-07-08")


def _complete_patch(**overrides: object) -> dict:
    doc: dict = {
        "target_housing": {"target_region": "경기 고양", "desired_size_sqm": 59.0},
        "user_profile": {
            "age": 34,
            "is_head_of_household": True,
            "residence_area": "경기",
            "residence_years_in_region": 4,
            "homeless_duration_months": 72,
            "marriage": {"is_married": True, "marriage_date": "2021-03-10"},
            "children_count": 2,
            "infants_count": 1,
            "has_child_under_2": True,
            "dependents_count": 3,
            "income_and_assets": {"monthly_income_krw": 7_500_000, "is_dual_income": True},
        },
        "subscription_account": {
            "duration_months": 72,
            "payment_count": 70,
            "total_balance_krw": 18_000_000,
            "spouse_duration_months": 36,
        },
    }
    for path, value in overrides.items():
        node = doc
        parts = path.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return doc


def _mock_odcloud_routes():
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


async def test_recommend_requires_session():
    result = await recommend.recommend_housing("nope")
    assert result["status"] == "session_not_found"
    assert "update_my_profile" in result["guidance"]


async def test_recommend_incomplete_profile_returns_questions():
    session_id, _ = store_module.default_store.upsert(None, {"user_profile": {"age": 34}})
    result = await recommend.recommend_housing(session_id)
    assert result["status"] == "needs_more_info"


@respx.mock
async def test_recommend_ranks_public_notice_for_eligible_user():
    _mock_odcloud_routes()
    session_id, _ = store_module.default_store.upsert(None, _complete_patch())

    result = await recommend.recommend_housing(session_id, max_candidates_to_scan=5, top_n=3)

    assert result["status"] == "ok"
    assert result["total_candidates_scanned"] == 1
    assert len(result["recommendations"]) == 1
    top = result["recommendations"][0]
    assert top["notice"]["HOUSE_MANAGE_NO"] == "2026000320"
    assert top["track"] == "public"  # 고양창릉 공고는 '국민'
    assert top["application_status"] == "접수전"  # 접수시작 2026-07-20 (오늘 2026-07-08)
    assert "Probability" in top["feasibility"]
    assert top["past_competition"], "과거 경쟁률이 붙어 있어야 한다"
    # 프로필 요약과 검증 주의도 함께 돌려줘 클라이언트 AI가 설명에 쓸 수 있게 한다
    assert result["analysis_summary"]["private_general_score"] == 45
    assert result["verification_notes"]


@respx.mock
async def test_recommend_skips_public_notice_for_homeowner():
    """유주택자(공공 전체 부적격)는 '국민' 공고가 추천에서 걸러져야 한다."""
    _mock_odcloud_routes()
    session_id, _ = store_module.default_store.upsert(
        None,
        _complete_patch(
            **{
                "user_profile.homeless_duration_months": 0,
                "user_profile.has_child_under_2": False,
                "user_profile.children_count": 0,
            }
        ),
    )

    result = await recommend.recommend_housing(session_id, max_candidates_to_scan=5)

    assert result["status"] == "ok"
    assert result["recommendations"] == []
    assert result["skipped_ineligible_count"] == 1


@respx.mock
async def test_recommend_excludes_closed_notice():
    """접수가 끝난 공고는 신청 불가이므로 추천에서 빠지고 skipped_closed_count로 집계된다."""
    detail = _load_fixture("apt_lttot_pblanc_detail.json")
    detail["data"][0] = {
        **detail["data"][0],
        "RCEPT_BGNDE": "2026-05-01",
        "RCEPT_ENDDE": "2026-05-10",  # 오늘(2026-07-08) 이전이라 마감
    }
    respx.get("https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/getAPTLttotPblancDetail").mock(
        return_value=httpx.Response(200, json=detail)
    )
    session_id, _ = store_module.default_store.upsert(None, _complete_patch())

    result = await recommend.recommend_housing(session_id, max_candidates_to_scan=5)

    assert result["status"] == "ok"
    assert result["recommendations"] == []
    assert result["skipped_closed_count"] == 1

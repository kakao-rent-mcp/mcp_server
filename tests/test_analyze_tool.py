"""analyze_my_subscription 도구 테스트 — 세션 프로필을 룰 엔진에 연결한다."""

from __future__ import annotations

import pytest

from slug_mcp import store as store_module
from slug_mcp.store import ProfileStore
from slug_mcp.tools import analyze as analyze_tools


@pytest.fixture(autouse=True)
def _fresh_store(monkeypatch):
    monkeypatch.setattr(store_module, "default_store", ProfileStore())


def _complete_patch() -> dict:
    return {
        "target_housing": {"target_region": "서울 마포구", "desired_size_sqm": 59.0},
        "user_profile": {
            "age": 34,
            "is_head_of_household": True,
            "residence_area": "서울",
            "residence_years_in_region": 4,
            "homeless_duration_months": 72,
            "owned_house_count": 0,
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


def test_analyze_unknown_session_guides_user():
    result = analyze_tools.analyze_my_subscription("nope")
    assert result["status"] == "session_not_found"
    assert "update_my_profile" in result["guidance"]


def test_analyze_incomplete_profile_returns_questions():
    session_id, _ = store_module.default_store.upsert(None, {"user_profile": {"age": 34}})
    result = analyze_tools.analyze_my_subscription(session_id)
    assert result["status"] == "needs_more_info"
    assert result["missing_required_fields"]


def test_analyze_complete_profile_returns_scores():
    session_id, _ = store_module.default_store.upsert(None, _complete_patch())
    result = analyze_tools.analyze_my_subscription(session_id)
    assert result["status"] == "ok"
    assert result["scores"]["private_general_score"] == 43  # C-1 무주택 기산 상한 반영
    assert result["matching_analysis"]["recommended_tracks"]

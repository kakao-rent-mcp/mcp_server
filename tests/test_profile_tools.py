"""프로필 설정 도구(update_my_profile / get_my_profile) 테스트.

사용자가 대화 몇 번에 걸쳐 정보를 조각조각 말해도, 클라이언트 AI가
update_my_profile을 반복 호출하며 프로필이 누적되고, 응답의
next_questions로 다음에 물어볼 것을 안내받는 흐름을 검증한다.
"""

from __future__ import annotations

import pytest

from slug_mcp import store as store_module
from slug_mcp.models import Marriage, SubscriptionAccount, TargetHousing, UserProfile
from slug_mcp.store import ProfileStore
from slug_mcp.tools import profile as profile_tools


@pytest.fixture(autouse=True)
def _fresh_store(monkeypatch):
    monkeypatch.setattr(store_module, "default_store", ProfileStore())


def test_first_update_creates_session_and_lists_missing_fields():
    result = profile_tools.update_my_profile(
        user_profile=UserProfile(age=34, residence_area="서울")
    )
    assert result["session_id"]
    assert result["ready_for_analysis"] is False
    missing = [item["field"] for item in result["missing_required_fields"]]
    assert "user_profile.age" not in missing  # 이미 채워짐
    assert "subscription_account.duration_months" in missing
    assert result["next_questions"], "다음에 물어볼 질문을 안내해야 한다"


def test_updates_accumulate_across_calls():
    first = profile_tools.update_my_profile(user_profile=UserProfile(age=34))
    session_id = first["session_id"]

    second = profile_tools.update_my_profile(
        session_id=session_id,
        user_profile=UserProfile(marriage=Marriage(is_married=True)),
    )
    assert second["profile"]["user_profile"]["age"] == 34  # 이전 값 유지
    assert second["profile"]["user_profile"]["marriage"]["is_married"] is True


def test_profile_becomes_ready_when_required_fields_filled():
    result = profile_tools.update_my_profile(
        target_housing=TargetHousing(target_region="서울 마포구", desired_size_sqm=59),
        user_profile=UserProfile(
            age=34,
            residence_area="서울",
            homeless_duration_months=72,
            owned_house_count=0,
            marriage=Marriage(is_married=True),
            dependents_count=3,
        ),
        subscription_account=SubscriptionAccount(duration_months=72, total_balance_krw=18_000_000),
    )
    # monthly_income_krw 하나만 남음
    assert result["ready_for_analysis"] is False
    session_id = result["session_id"]

    from slug_mcp.models import IncomeAssets

    final = profile_tools.update_my_profile(
        session_id=session_id,
        user_profile=UserProfile(income_and_assets=IncomeAssets(monthly_income_krw=7_500_000)),
    )
    assert final["ready_for_analysis"] is True
    assert final["missing_required_fields"] == []


def test_reset_clears_session():
    first = profile_tools.update_my_profile(user_profile=UserProfile(age=34))
    session_id = first["session_id"]
    result = profile_tools.update_my_profile(session_id=session_id, reset=True)
    assert result["profile"]["user_profile"].get("age") is None


def test_get_my_profile_round_trip():
    created = profile_tools.update_my_profile(user_profile=UserProfile(age=34))
    fetched = profile_tools.get_my_profile(created["session_id"])
    assert fetched["profile"]["user_profile"]["age"] == 34


def test_get_my_profile_unknown_session_guides_user():
    result = profile_tools.get_my_profile("nope")
    assert result["found"] is False
    assert "update_my_profile" in result["guidance"]


def test_ready_for_provisional_with_core_only():
    """core 5항목만 채우면 잠정 판정 준비 완료(정밀 판정은 full까지 필요)."""
    result = profile_tools.update_my_profile(
        target_housing=TargetHousing(target_region="부산"),
        user_profile=UserProfile(age=34, residence_area="부산", owned_house_count=0),
        subscription_account=SubscriptionAccount(duration_months=24),
    )
    assert result["ready_for_provisional"] is True
    assert result["ready_for_analysis"] is False  # 소득·예치금 등 full 미입력
    recommended = [item["field"] for item in result["missing_recommended_fields"]]
    assert "user_profile.income_and_assets.monthly_income_krw" in recommended
    assert "subscription_account.total_balance_krw" in recommended


def test_birth_date_satisfies_age_requirement():
    """생년월일을 주면 age 미입력이어도 core가 충족된다(둘 중 하나면 됨)."""
    result = profile_tools.update_my_profile(
        target_housing=TargetHousing(target_region="부산"),
        user_profile=UserProfile(
            birth_date="1995-10-24", residence_area="부산", owned_house_count=0
        ),
        subscription_account=SubscriptionAccount(duration_months=24),
    )
    missing = [item["field"] for item in result["missing_required_fields"]]
    assert "user_profile.age" not in missing
    assert result["ready_for_provisional"] is True

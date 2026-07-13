"""임대 트랙 정보 유도 테스트.

사용자가 임대주택을 물으면(track="rental") 분양과 동일한 흐름으로
next_questions가 임대에 맞는 질문(소득·수급자격·유형별 통장)을 안내하고,
분양 트랙(track 미지정)의 기존 동작은 그대로임을 검증한다.
"""

from __future__ import annotations

import pytest

from slug_mcp import store as store_module
from slug_mcp.models import (
    CORE_FIELD_QUESTIONS,
    HousingTrack,
    IncomeAssets,
    RentalType,
    TargetHousing,
    UserProfile,
    WelfareStatus,
    missing_fields,
)
from slug_mcp.store import ProfileStore
from slug_mcp.tools import analyze as analyze_tools
from slug_mcp.tools import profile as profile_tools


@pytest.fixture(autouse=True)
def _fresh_store(monkeypatch):
    monkeypatch.setattr(store_module, "default_store", ProfileStore())


def _missing_paths(result: dict, key: str = "missing_required_fields") -> list[str]:
    return [item["field"] for item in result[key]]


def test_rental_track_asks_rental_type_first_and_skips_subscription():
    result = profile_tools.update_my_profile(
        target_housing=TargetHousing(track=HousingTrack.RENTAL)
    )
    missing = _missing_paths(result)
    # 유형을 모르면 통장 요건을 정할 수 없으므로 유형 질문이 가장 먼저 나온다.
    assert missing[0] == "target_housing.rental_type"
    assert result["next_questions"][0].startswith("어떤 임대주택")
    # 유형 확정 전에는 청약통장 질문이 core에 없다.
    assert not any(path.startswith("subscription_account") for path in missing)
    # 임대는 소득·수급자격이 core다.
    assert "user_profile.income_and_assets.monthly_income_krw" in missing
    assert "user_profile.welfare.is_basic_living_recipient" in missing


def test_permanent_rental_never_asks_subscription_account():
    result = profile_tools.update_my_profile(
        target_housing=TargetHousing(track=HousingTrack.RENTAL, rental_type=RentalType.PERMANENT)
    )
    all_missing = (
        _missing_paths(result)
        + _missing_paths(result, "missing_recommended_fields")
        + _missing_paths(result, "missing_optional_fields")
    )
    # 영구임대는 수급자 순위제라 청약통장이 아예 필요 없다.
    assert not any(path.startswith("subscription_account") for path in all_missing)


def test_national_rental_asks_payment_count_as_core():
    result = profile_tools.update_my_profile(
        target_housing=TargetHousing(track=HousingTrack.RENTAL, rental_type=RentalType.NATIONAL)
    )
    # 국민임대는 납입인정횟수로 순위를 정하므로 core로 묻는다.
    assert "subscription_account.payment_count" in _missing_paths(result)


def test_happy_rental_asks_account_duration_as_core():
    result = profile_tools.update_my_profile(
        target_housing=TargetHousing(track=HousingTrack.RENTAL, rental_type=RentalType.HAPPY)
    )
    assert "subscription_account.duration_months" in _missing_paths(result)


def test_sale_track_questions_unchanged_by_rental_feature():
    # track 미지정 = 분양. 기존 분양 core 질문표와 완전히 동일해야 한다(회귀 가드).
    core, _full, _optional = missing_fields({})
    assert [item["field"] for item in core] == list(CORE_FIELD_QUESTIONS)


def test_filled_rental_profile_guides_to_lease_tools():
    result = profile_tools.update_my_profile(
        target_housing=TargetHousing(
            track=HousingTrack.RENTAL,
            rental_type=RentalType.PERMANENT,
            target_region="성남시",
        ),
        user_profile=UserProfile(
            age=68,
            residence_area="경기",
            residence_years_in_region=5,
            owned_house_count=0,
            is_single_household=True,
            welfare=WelfareStatus(is_basic_living_recipient=True),
            income_and_assets=IncomeAssets(
                monthly_income_krw=1_500_000,
                total_real_estate_krw=0,
                car_value_krw=0,
            ),
        ),
    )
    assert result["missing_required_fields"] == []
    assert result["missing_recommended_fields"] == []
    # 임대는 자동판정 미지원이므로 공고문 원문 대조 도구를 안내한다.
    assert "extract_lease_notice_text" in result["guidance"]


def test_analyze_refuses_rental_track_explicitly():
    created = profile_tools.update_my_profile(
        target_housing=TargetHousing(track=HousingTrack.RENTAL, rental_type=RentalType.NATIONAL)
    )
    result = analyze_tools.analyze_my_subscription(created["session_id"])
    # 분양 룰로 오판정하지 않고 명시적으로 미지원을 알린다.
    assert result["status"] == "rental_not_supported"
    assert "extract_lease_notice_text" in result["guidance"]

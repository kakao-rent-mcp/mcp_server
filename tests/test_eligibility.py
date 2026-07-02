from __future__ import annotations

from kr_housing_mcp import models
from kr_housing_mcp.tools import eligibility


def _base_profile(**overrides: object) -> models.UserProfile:
    defaults: dict[str, object] = dict(
        household_size=3,
        annual_household_income_10k_won=5000,
        real_estate_value_10k_won=0,
        vehicle_value_10k_won=0,
        has_no_house=True,
        marital_status=models.MaritalStatus.NEWLYWED,
        region="서울",
        subscription_account=models.SubscriptionAccount(
            account_type=models.SubscriptionAccountType.PUBLIC,
            joined_months_ago=24,
            payment_count=24,
        ),
    )
    defaults.update(overrides)
    return models.UserProfile(**defaults)


def test_income_table_not_configured_forces_manual_review():
    """eligibility_rules.yaml의 소득기준표가 비어 있는 현재 상태를 그대로 검증한다.

    이 표가 채워지면(README의 TODO 참고) 이 테스트는 실패하게 되고, 그때
    test_all_conditions_pass_once_income_table_is_configured 쪽 로직을 실제
    _load_rules()로 옮기면 된다.
    """
    result = eligibility.check_eligibility(_base_profile())
    assert result.needs_manual_review is True
    assert any("소득기준표" in reason for reason in result.reasons_fail)


def test_no_house_condition_fails():
    result = eligibility.check_eligibility(_base_profile(has_no_house=False))
    assert any("무주택" in reason for reason in result.reasons_fail)


def test_real_estate_asset_over_limit_fails():
    result = eligibility.check_eligibility(_base_profile(real_estate_value_10k_won=999_999))
    assert any("부동산 자산" in reason for reason in result.reasons_fail)


def test_vehicle_asset_over_limit_fails():
    result = eligibility.check_eligibility(_base_profile(vehicle_value_10k_won=999_999))
    assert any("자동차 자산" in reason for reason in result.reasons_fail)


def test_public_account_below_rank1_requirement_fails():
    profile = _base_profile(
        region="경기",
        subscription_account=models.SubscriptionAccount(
            account_type=models.SubscriptionAccountType.PUBLIC,
            joined_months_ago=3,
            payment_count=3,
        ),
    )
    result = eligibility.check_eligibility(profile)
    assert any("가입기간" in reason for reason in result.reasons_fail)
    assert any("납입횟수" in reason for reason in result.reasons_fail)


def test_private_account_deposit_shortfall_fails():
    profile = _base_profile(
        region="서울",
        subscription_account=models.SubscriptionAccount(
            account_type=models.SubscriptionAccountType.PRIVATE,
            joined_months_ago=36,
            deposit_amount_10k_won=100,
        ),
    )
    result = eligibility.check_eligibility(profile, target_exclusive_area_sqm=59.9)
    assert any("예치금" in reason for reason in result.reasons_fail)


def test_private_account_deposit_check_skipped_without_target_area():
    profile = _base_profile(
        region="서울",
        subscription_account=models.SubscriptionAccount(
            account_type=models.SubscriptionAccountType.PRIVATE,
            joined_months_ago=36,
            deposit_amount_10k_won=100,
        ),
    )
    result = eligibility.check_eligibility(profile)
    assert not any("예치금" in reason for reason in result.reasons_fail)


def test_all_conditions_pass_once_income_table_is_configured(monkeypatch):
    """소득기준표(TODO)가 채워졌다고 가정했을 때 정상 합격 경로가 동작하는지 확인한다."""
    fake_rules = {
        **eligibility._load_rules(),
        "median_monthly_income_by_household_size": {"3": 700},
    }
    monkeypatch.setattr(eligibility, "_load_rules", lambda: fake_rules)

    profile = _base_profile(
        region="서울",
        annual_household_income_10k_won=5000,  # 700만원 * 12 * 130% = 10,920만원 한도 이내
        subscription_account=models.SubscriptionAccount(
            account_type=models.SubscriptionAccountType.PUBLIC,
            joined_months_ago=24,
            payment_count=24,
        ),
    )
    result = eligibility.check_eligibility(profile)
    assert result.passed is True
    assert result.needs_manual_review is False

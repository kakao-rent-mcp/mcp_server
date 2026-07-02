"""자격판정 규칙 엔진.

config/eligibility_rules.yaml에 있는 기준값을 읽어 사용자 프로필을 판정한다.
이 파일 자체는 순수 계산만 하며 네트워크 호출이 없다.
"""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from typing import Any

import yaml

from ..models import EligibilityResult, MaritalStatus, SubscriptionAccountType, UserProfile

_SUPPLY_TYPE_BY_MARITAL_STATUS: dict[MaritalStatus, str] = {
    MaritalStatus.PRE_NEWLYWED: "newlywed",
    MaritalStatus.NEWLYWED: "newlywed",
    MaritalStatus.HAS_CHILD: "newborn",
    MaritalStatus.MULTI_CHILD: "multi_child",
    MaritalStatus.NONE: "generic",
}

# 수도권 여부는 공공주택 청약통장 1순위 요건(가입기간/납입횟수)을 가를 때 쓰인다.
_CAPITAL_REGIONS = {"서울", "경기", "인천"}


@lru_cache(maxsize=1)
def _load_rules() -> dict[str, Any]:
    path = resources.files("kr_housing_mcp.config").joinpath("eligibility_rules.yaml")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _region_tier(rules: dict[str, Any], region: str) -> str:
    tiers = rules["region_tier"]
    if region in tiers.get("tier1", []):
        return "tier1"
    if region in tiers.get("tier2", []):
        return "tier2"
    return "tier3"


def _deposit_area_bracket(area_sqm: float) -> str:
    if area_sqm <= 85:
        return "85"
    if area_sqm <= 102:
        return "102"
    if area_sqm <= 135:
        return "135"
    return "all"


def check_eligibility(
    profile: UserProfile,
    supply_type: str | None = None,
    target_exclusive_area_sqm: float | None = None,
) -> EligibilityResult:
    """사용자 프로필이 특정 특별공급 유형의 자격 기준을 만족하는지 판정한다.

    Args:
        profile: 판정할 사용자 정보
        supply_type: 판정 대상 공급유형
            (generic/newlywed/multi_child/old_parents/newborn/first_time).
            비우면 profile.marital_status로 자동 추정한다.
        target_exclusive_area_sqm: 민영주택 예치금 판정에 쓸 전용면적(㎡).
            비우면 예치금 판정은 건너뛰고 나머지 조건만 판정한다.
    """
    rules = _load_rules()
    resolved_supply_type = supply_type or _SUPPLY_TYPE_BY_MARITAL_STATUS[profile.marital_status]

    reasons_pass: list[str] = []
    reasons_fail: list[str] = []
    needs_manual_review = False

    if not profile.has_no_house:
        reasons_fail.append("무주택 세대구성원 조건을 충족하지 않습니다.")
    else:
        reasons_pass.append("무주택 세대구성원 조건 충족")

    asset_limits = rules["asset_limits"]
    if profile.real_estate_value_10k_won > asset_limits["real_estate_10k_won"]:
        reasons_fail.append(
            f"부동산 자산({profile.real_estate_value_10k_won}만원)이 "
            f"기준({asset_limits['real_estate_10k_won']}만원)을 초과합니다."
        )
    else:
        reasons_pass.append("부동산 자산 기준 충족")

    if profile.vehicle_value_10k_won > asset_limits["vehicle_10k_won"]:
        reasons_fail.append(
            f"자동차 자산({profile.vehicle_value_10k_won}만원)이 "
            f"기준({asset_limits['vehicle_10k_won']}만원)을 초과합니다."
        )
    else:
        reasons_pass.append("자동차 자산 기준 충족")

    income_ratio = rules["income_ratio_by_supply_type"].get(resolved_supply_type)
    baseline = rules["median_monthly_income_by_household_size"].get(str(profile.household_size))
    if income_ratio is None:
        reasons_fail.append(f"알 수 없는 공급유형입니다: {resolved_supply_type}")
    elif baseline is None:
        needs_manual_review = True
        reasons_fail.append(
            "소득기준표(median_monthly_income_by_household_size)가 아직 설정되지 않아 "
            "소득 조건을 자동판정하지 못했습니다. 관리자가 최신 기준표를 채워야 합니다."
        )
    else:
        income_limit = baseline * 12 * income_ratio / 100
        if profile.annual_household_income_10k_won > income_limit:
            reasons_fail.append(
                f"가구소득({profile.annual_household_income_10k_won}만원/년)이 "
                f"기준({income_limit:.0f}만원/년, 중위소득의 {income_ratio}%)을 초과합니다."
            )
        else:
            reasons_pass.append("소득 기준 충족")

    account = profile.subscription_account
    account_rules = rules["subscription_account"][account.account_type.value]
    if account.account_type == SubscriptionAccountType.PRIVATE:
        if target_exclusive_area_sqm is None:
            reasons_pass.append("청약통장(민영): 대상 면적이 주어지지 않아 예치금 판정은 건너뜀")
        elif account.deposit_amount_10k_won is None:
            reasons_fail.append("민영주택 청약통장의 예치금 정보가 없습니다.")
        else:
            tier = _region_tier(rules, profile.region)
            bracket = _deposit_area_bracket(target_exclusive_area_sqm)
            required = account_rules["deposit_table_10k_won"][bracket][tier]
            if account.deposit_amount_10k_won < required:
                reasons_fail.append(
                    f"청약통장 예치금({account.deposit_amount_10k_won}만원)이 "
                    f"{profile.region}·전용 {target_exclusive_area_sqm}㎡ 기준"
                    f"({required}만원)에 못 미칩니다."
                )
            else:
                reasons_pass.append("청약통장(민영) 예치금 기준 충족")
    else:
        # 투기과열지구 세분화는 지역코드 매핑이 추가로 필요해 1차 버전에서는
        # 수도권/비수도권 두 단계만 구분한다.
        rank1 = account_rules["rank1_requirement"]
        is_capital = profile.region in _CAPITAL_REGIONS
        req = rank1["capital_region"] if is_capital else rank1["non_capital_region"]
        if account.joined_months_ago < req["min_months"]:
            reasons_fail.append(
                f"청약통장 가입기간({account.joined_months_ago}개월)이 "
                f"1순위 기준({req['min_months']}개월)에 못 미칩니다."
            )
        else:
            reasons_pass.append("청약통장(공공) 가입기간 기준 충족")

        payments = account.payment_count or 0
        min_payments = req["min_payments"]
        if payments < min_payments:
            reasons_fail.append(
                f"청약통장 납입횟수({payments}회)가 1순위 기준({min_payments}회)에 못 미칩니다."
            )
        else:
            reasons_pass.append("청약통장(공공) 납입횟수 기준 충족")

    return EligibilityResult(
        supply_type=resolved_supply_type,
        passed=len(reasons_fail) == 0,
        reasons_pass=reasons_pass,
        reasons_fail=reasons_fail,
        needs_manual_review=needs_manual_review,
    )

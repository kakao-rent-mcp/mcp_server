"""임대주택(영구·국민·행복·공공) 자격 판정 엔진 — rental_rules.yaml 기반 순수 계산.

분양 engine.py와 분리한 이유: 분양은 경쟁 점수 계산(가점·납입총액), 임대는
기준표 대조 + 순위 결정으로 판정 구조가 다르다. 기준값은 일반 고시 기준이므로
판정 결과에는 항상 공고문 대조 안내를 붙인다 (docs/rental-policy-spec.md).
"""

from __future__ import annotations

from typing import Any

# 행복주택 계층 → 자산 상한 키 (한부모는 신혼부부와 동일 상한을 쓴다).
_HAPPY_ASSET_KEY = {
    "youth": "youth",
    "newlywed": "newlywed",
    "single_parent": "newlywed",
    "elderly": "elderly",
    "welfare_recipient": "welfare_recipient",
    "college_student": "college_student",
}


def rental_income_ratio_pct(
    monthly_income_krw: int, household_size: int, rules: dict[str, Any]
) -> float | None:
    """세전 월소득이 임대용 도시근로자 월평균소득(가구원수별 개별 행)의 몇 %인지.

    분양 소득표(scoring.income_ratio_pct, 3인 이하 통합 행)와 표가 다르다 — 혼용 금지.
    8인 이상 가구는 고시 미확인이라 None(판정 불가)을 돌려준다.
    """
    table: dict[str, int] = rules["rental_income_100pct_krw"]
    baseline = table.get(str(max(1, household_size)))
    if baseline is None:
        return None
    return monthly_income_krw / baseline * 100


def income_within_cap(
    income_ratio: float | None,
    cap_pct: float | None,
    household_size: int,
    rules: dict[str, Any],
) -> bool | None:
    """유형별 소득 상한(%)에 1·2인 가구 가산(%p)을 더해 충족 여부를 본다.

    None = 판정 불가 — 상한이 없는 계층(cap_pct=None)이거나 소득표 밖(income_ratio=None).
    """
    if cap_pct is None or income_ratio is None:
        return None
    bonus = rules["household_income_bonus_pct"].get(str(max(1, household_size)), 0)
    return income_ratio <= cap_pct + bonus


def check_assets(
    rental_type: str,
    happy_tier: str | None,
    real_estate_krw: int | None,
    car_value_krw: int | None,
    rules: dict[str, Any],
) -> list[dict[str, str]]:
    """유형별 자산·자동차 상한 위반 목록. 빈 리스트 = 통과.

    수집 필드는 부동산·자동차뿐이라 '총자산' 기준은 하한 검사만 한다: 부동산만으로
    총자산 상한을 넘으면 확정 탈락이고, 넘지 않으면 통과시키되 금융자산 포함 여부는
    호출자가 공고문 대조 안내로 넘긴다.
    """
    limits_root = rules["asset_limits_10k_won"]
    if rental_type == "happy":
        limits = limits_root["happy"][_HAPPY_ASSET_KEY[happy_tier or "newlywed"]]
    else:
        limits = limits_root[rental_type]

    violations: list[dict[str, str]] = []
    asset_cap_key = "real_estate" if "real_estate" in limits else "total_asset"
    asset_cap = limits[asset_cap_key] * 10_000
    label = "부동산" if asset_cap_key == "real_estate" else "총자산"
    if real_estate_krw is not None and real_estate_krw > asset_cap:
        violations.append(
            {
                "filter": "asset",
                "reason": f"보유 부동산 {real_estate_krw:,}원이 {label} 상한 "
                f"{asset_cap:,}원을 초과합니다.",
            }
        )
    vehicle_cap = limits["vehicle"] * 10_000
    if car_value_krw is not None and car_value_krw > vehicle_cap:
        reason = (
            "이 계층은 자동차를 소유할 수 없습니다."
            if vehicle_cap == 0
            else f"자동차 가액 {car_value_krw:,}원이 상한 {vehicle_cap:,}원을 초과합니다."
        )
        violations.append({"filter": "vehicle", "reason": reason})
    return violations

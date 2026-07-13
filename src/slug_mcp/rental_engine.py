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


def judge_permanent(
    *,
    age: int,
    income_ratio: float | None,
    household_size: int,
    is_basic_living_recipient: bool | None,
    is_national_merit: bool | None,
    is_near_poverty: bool | None,
    is_single_parent: bool,
    rules: dict[str, Any],
) -> dict[str, Any]:
    """영구임대 순위 판정 — 수급자 중심 순위제, 청약통장 불필요.

    프로필로 판정 가능한 1순위 자격만 본다. 북한이탈주민 등 필드가 없는 카테고리는
    스펙의 '뺀 것' — 해당자는 공고문 대조를 안내한다 (orchestrator의 공통 노트).
    """
    cfg = rules["permanent"]
    notes: list[str] = []
    if is_basic_living_recipient:
        return {"eligible": True, "rank": 1, "basis": "생계·의료급여 수급자", "notes": notes}
    if is_single_parent:
        return {"eligible": True, "rank": 1, "basis": "지원대상 한부모가족", "notes": notes}
    merit_ok = income_within_cap(
        income_ratio, cfg["rank1"]["national_merit_income_pct"], household_size, rules
    )
    if is_national_merit and merit_ok:
        return {"eligible": True, "rank": 1, "basis": "국가유공자(소득 70% 이하)", "notes": notes}
    if is_near_poverty and age >= 65:
        return {
            "eligible": True,
            "rank": 1,
            "basis": "만 65세 이상 수급권자·차상위",
            "notes": notes,
        }
    if income_within_cap(income_ratio, cfg["rank2"]["income_pct"], household_size, rules):
        notes.append(
            "영구임대 2순위의 1·2인 가구 소득 가산은 고시 표기가 엇갈려 공통 가산"
            "(+20/+10%p)을 적용했습니다 — 공고문 기준을 확인하세요."
        )
        return {"eligible": True, "rank": 2, "basis": "소득 50% 이하(가산 반영)", "notes": notes}
    return {
        "eligible": False,
        "rank": None,
        "basis": "수급·한부모·유공자 자격이 없고 소득이 2순위 상한을 초과합니다.",
        "notes": notes,
    }


def _bracket_points(bracket: dict[int, int], value: int) -> int:
    """{하한: 점수} 표에서 value가 충족하는 최고 점수를 고른다 (미달이면 0)."""
    return max((pts for threshold, pts in bracket.items() if value >= threshold), default=0)


def national_tiebreak_score(
    *,
    age: int,
    dependents_count: int,
    residence_years: int,
    children_count: int,
    payment_count: int,
    rules: dict[str, Any],
) -> dict[str, Any]:
    """국민임대 동일순위 경쟁 시 배점. 고령부양 항목은 프로필 미수집이라 0점 처리."""
    table = rules["national"]["tiebreak_score_table"]
    score = {
        "age": _bracket_points(table["age"], age),
        "dependents": _bracket_points(table["dependents"], dependents_count),
        "residence_years": _bracket_points(table["residence_years"], residence_years),
        "minor_children": _bracket_points(table["minor_children"], children_count),
        "payment_count": _bracket_points(table["payment_count"], payment_count),
        "elderly_care": 0,
    }
    notes = [
        "고령부양 배점(65세 이상 직계존속 1년 이상 부양, 3점)은 수집 항목이 아니라 "
        "0점으로 두었습니다 — 해당되면 실제 배점이 3점 높습니다."
    ]
    return {**score, "total": sum(score.values()), "notes": notes}


def judge_national(
    *,
    income_ratio: float | None,
    household_size: int,
    desired_size_sqm: float | None,
    account_months: int,
    payment_count: int,
    age: int,
    dependents_count: int,
    residence_years: int,
    children_count: int,
    rules: dict[str, Any],
) -> dict[str, Any]:
    """국민임대: 소득컷(70%) → 면적별 순위(50㎡ 미만 거주지 / 이상 통장) → 동순위 배점."""
    cfg = rules["national"]
    notes: list[str] = []
    tiebreak = national_tiebreak_score(
        age=age,
        dependents_count=dependents_count,
        residence_years=residence_years,
        children_count=children_count,
        payment_count=payment_count,
        rules=rules,
    )
    income_ok = income_within_cap(income_ratio, cfg["income_pct"], household_size, rules)
    if income_ok is False:
        return {
            "eligible": False,
            "rank": None,
            "basis": f"가구 소득이 국민임대 상한({cfg['income_pct']}% + 1·2인 가산)을 초과합니다.",
            "notes": notes,
            "tiebreak": tiebreak,
        }
    if income_ok is None:
        notes.append("소득표 밖(8인 이상 가구 등)이라 소득요건은 공고문으로 확인해야 합니다.")

    if desired_size_sqm is not None and desired_size_sqm < 50:
        priority_ok = income_within_cap(
            income_ratio, cfg["income_pct_priority_under_50sqm"], household_size, rules
        )
        if priority_ok:
            notes.append("소득 50% 이하 — 전용 50㎡ 미만 우선공급 대상입니다.")
        notes.append(
            "전용 50㎡ 미만은 거주 시·군·구로 순위가 갈립니다(당해 1순위/연접 2순위/기타 "
            "3순위) — 거주지가 공고 지역과 같은 시·군인지 공고문으로 확인하세요."
        )
        return {
            "eligible": True,
            "rank": None,
            "basis": "소득요건 충족(50㎡ 미만, 거주지 순위)",
            "notes": notes,
            "tiebreak": tiebreak,
        }

    if desired_size_sqm is None:
        notes.append("희망 전용면적 미입력 — 50㎡ 이상(통장 순위) 기준으로 판정했습니다.")
    rank_cfg = cfg["rank_50sqm_or_more"]
    if (
        account_months >= rank_cfg["rank1"]["account_months"]
        and payment_count >= rank_cfg["rank1"]["payment_count"]
    ):
        rank = 1
    elif (
        account_months >= rank_cfg["rank2"]["account_months"]
        and payment_count >= rank_cfg["rank2"]["payment_count"]
    ):
        rank = 2
    else:
        rank = 3
    return {
        "eligible": True,
        "rank": rank,
        "basis": f"소득요건 충족, 통장 기준 {rank}순위",
        "notes": notes,
        "tiebreak": tiebreak,
    }

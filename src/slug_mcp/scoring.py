"""청약 가점·배점 순수 계산 함수 모음.

네트워크·상태 없이 입력만으로 계산한다. 산식 근거와 검증 상태는
docs/subscription-policy-spec.md(§2.B, §3.B)를 따른다.
"""

from __future__ import annotations

from typing import Any

# --- 민영주택 일반공급 가점 (84점 만점, §3.B — 🟢 청약홈 확인) --------------


def score_homeless_period(age: int, is_married: bool, homeless_duration_months: int) -> int:
    """무주택 기간 가점 (최대 32점). 만 30세(또는 그 전 혼인)부터 산정한다."""
    if not is_married and age < 30:
        return 0
    if homeless_duration_months <= 0:  # 유주택
        return 0
    years = homeless_duration_months // 12
    if years < 1:
        return 2
    if years >= 15:
        return 32
    return years * 2 + 2


def score_dependents(dependents_count: int) -> int:
    """부양가족 가점 (최대 35점). 0명=5점, 1명당 +5."""
    return min(35, dependents_count * 5 + 5)


def score_subscription_period(duration_months: int, spouse_duration_months: int = 0) -> int:
    """청약통장 가입기간 가점 (본인+배우자 합산, 상한 17점).

    배우자 가입기간 50% 인정(최대 3점)은 2024-03-25 시행.
    """
    if duration_months <= 0:
        return 0
    years = duration_months // 12
    if duration_months < 6:
        self_score = 1
    elif years < 1:
        self_score = 2
    elif years >= 15:
        self_score = 17
    else:
        self_score = years + 2

    spouse_years = spouse_duration_months // 12
    if spouse_duration_months <= 0:
        spouse_score = 0
    elif spouse_years < 1:
        spouse_score = 1
    elif spouse_years < 2:
        spouse_score = 2
    else:
        spouse_score = 3
    return min(17, self_score + spouse_score)


def private_general_score(
    age: int,
    is_married: bool,
    homeless_duration_months: int,
    dependents_count: int,
    duration_months: int,
    spouse_duration_months: int = 0,
) -> dict[str, int]:
    """민영 일반공급 가점 합계와 항목별 점수를 돌려준다."""
    homeless = score_homeless_period(age, is_married, homeless_duration_months)
    dependents = score_dependents(dependents_count)
    subscription = score_subscription_period(duration_months, spouse_duration_months)
    return {
        "homeless_period": homeless,
        "dependents": dependents,
        "subscription_period": subscription,
        "total": homeless + dependents + subscription,
        "max": 84,
    }


# --- 다자녀 특별공급 배점 (100점 만점, §2.B.③ — 🟢) -------------------------


def _band_score(value: float, band_table: dict[str, int]) -> int:
    """{"기준값(이상)": 점수} 표에서 기준값이 큰 순서대로 첫 매치를 찾는다."""
    for threshold in sorted(band_table, key=lambda k: int(k), reverse=True):
        if value >= int(threshold):
            return band_table[threshold]
    return 0


def multi_child_score(
    children_count: int,
    infants_count: int,
    has_household_composition_bonus: bool,
    homeless_years: int,
    residence_years: int,
    account_years: int,
    table: dict[str, Any],
) -> dict[str, int]:
    """다자녀 특공 배점. 가입기간 상한 5점 + 세대구성 5점이 별도 존재(스펙 정정 반영)."""
    children = _band_score(children_count, table["children"])
    infants = _band_score(infants_count, table["infants"])
    composition = table["household_composition_bonus"] if has_household_composition_bonus else 0
    homeless = _band_score(homeless_years, table["homeless_years"])
    residence = _band_score(residence_years, table["residence_years"])
    account = _band_score(account_years, table["account_years"])
    total = children + infants + composition + homeless + residence + account
    return {
        "children": children,
        "infants": infants,
        "household_composition": composition,
        "homeless_period": homeless,
        "residence_period": residence,
        "account_period": account,
        "total": total,
        "max": 100,
    }


# --- 신혼부부 특별공급 배점 (LH 2026-07-05 확인: 우선 9점 / 일반 12점) -------


def newlywed_score(
    income_ratio_pct: float,
    residence_years: int,
    payment_count: int,
    children_count: int,
    homeless_years: int,
    table: dict[str, Any],
) -> dict[str, int]:
    """신혼부부 특공 배점.

    우선공급 경쟁 시 [가구소득+거주기간+납입횟수] 9점 만점,
    일반공급 경쟁 시 [거주기간+납입횟수+자녀수+무주택기간] 12점 만점.
    """
    t = table
    if income_ratio_pct <= t["income_ratio"]["high"]["max_ratio"]:
        income = t["income_ratio"]["high"]["points"]
    elif income_ratio_pct <= t["income_ratio"]["mid"]["max_ratio"]:
        income = t["income_ratio"]["mid"]["points"]
    else:
        income = t["income_ratio"]["low"]["points"]

    if residence_years >= t["residence_years"]["high"]["min_years"]:
        residence = t["residence_years"]["high"]["points"]
    elif residence_years >= t["residence_years"]["mid"]["min_years"]:
        residence = t["residence_years"]["mid"]["points"]
    else:
        residence = t["residence_years"]["low"]["points"]

    if payment_count >= t["payment_count"]["high"]["min_count"]:
        payments = t["payment_count"]["high"]["points"]
    elif payment_count >= t["payment_count"]["mid"]["min_count"]:
        payments = t["payment_count"]["mid"]["points"]
    elif payment_count >= t["payment_count"]["low"]["min_count"]:
        payments = t["payment_count"]["low"]["points"]
    else:
        payments = 0

    if children_count >= t["children_count"]["high"]["min_count"]:
        children = t["children_count"]["high"]["points"]
    elif children_count >= t["children_count"]["mid"]["min_count"]:
        children = t["children_count"]["mid"]["points"]
    elif children_count >= t["children_count"]["low"]["min_count"]:
        children = t["children_count"]["low"]["points"]
    else:
        children = 0

    if homeless_years >= t["homeless_years"]["high"]["min_years"]:
        homeless = t["homeless_years"]["high"]["points"]
    elif homeless_years >= t["homeless_years"]["mid"]["min_years"]:
        homeless = t["homeless_years"]["mid"]["points"]
    else:
        homeless = t["homeless_years"]["low"]["points"]

    return {
        "income": income,
        "residence_period": residence,
        "payment_count": payments,
        "children": children,
        "homeless_period": homeless,
        "priority_total": income + residence + payments,
        "priority_max": 9,
        "general_total": residence + payments + children + homeless,
        "general_max": 12,
    }


# --- 신생아 특별공급 소득 트랙 분기 (물량 70/20/10 — 🟢, 소득수치 🟡) --------


def newborn_track(income_ratio_pct: float, is_dual_income: bool, cfg: dict[str, Any]) -> str | None:
    """소득비율로 신생아 특공 우선/일반/추첨 트랙을 정한다. 초과 시 None(부적격)."""
    priority_cap = (
        cfg["priority"]["income_ratio_dual"] if is_dual_income else cfg["priority"]["income_ratio"]
    )
    general_cap = (
        cfg["general"]["income_ratio_dual"] if is_dual_income else cfg["general"]["income_ratio"]
    )
    if income_ratio_pct <= priority_cap:
        return "priority"
    if income_ratio_pct <= general_cap:
        return "general"
    if is_dual_income:
        return "lottery" if income_ratio_pct <= cfg["lottery"]["income_ratio_dual_max"] else None
    return "lottery"  # 외벌이 140% 초과는 자산요건 충족 전제 추첨 대상


# --- 가구원수별 소득비율 -----------------------------------------------------


def income_ratio_pct(monthly_income_krw: int, household_size: int, rules: dict[str, Any]) -> float:
    """세전 월소득이 도시근로자 가구원수별 월평균소득의 몇 %인지 계산한다."""
    table: dict[str, int] = rules["urban_worker_monthly_income_krw"]
    size = max(1, household_size)
    if str(size) in table:
        baseline = table[str(size)]
    else:
        baseline = table["8"] + (size - 8) * rules["extra_person_income_krw"]
    return monthly_income_krw / baseline * 100

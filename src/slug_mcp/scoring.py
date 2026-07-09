"""청약 가점·배점 순수 계산 함수 모음.

네트워크·상태 없이 입력만으로 계산한다. 산식 근거와 검증 상태는
docs/subscription-policy-spec.md(§2.B, §3.B)를 따른다.
"""

from __future__ import annotations

from typing import Any

# --- 민영주택 일반공급 가점 (84점 만점, §3.B — 🟢 청약홈 확인) --------------


def score_homeless_period(
    age: int,
    is_married: bool,
    homeless_duration_months: int,
    recognized_cap_months: int | None = None,
) -> int:
    """무주택 기간 가점 (최대 32점). 만 30세(또는 그 전 혼인)부터 산정한다.

    recognized_cap_months가 주어지면 입력 무주택기간을 그 상한으로 자른다(별표 1의
    "만 30세·혼인신고일 기산" 보정, 상한은 engine이 나이·혼인일로 계산). 생년월일이
    없어 만 나이 기준 근사이므로 최대 약 1년까지 과소 산정될 수 있다.
    """
    if not is_married and age < 30:
        return 0
    months = homeless_duration_months
    if recognized_cap_months is not None:
        months = min(months, max(0, recognized_cap_months))
    if months <= 0:  # 유주택이거나 기산 상한이 0
        return 0
    years = months // 12
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
    homeless_recognized_cap_months: int | None = None,
) -> dict[str, int]:
    """민영 일반공급 가점 합계와 항목별 점수를 돌려준다."""
    homeless = score_homeless_period(
        age, is_married, homeless_duration_months, homeless_recognized_cap_months
    )
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


# --- 신혼부부 특별공급 배점 (공공 일반형: 별표 6 순위제 + 경쟁 시 13점) -------


def _income_point(income_ratio_pct: float, is_dual_income: bool, cfg: dict[str, Any]) -> int:
    """가구소득 배점: 외벌이 single_max%·맞벌이 dual_max% 이하면 points, 초과 0."""
    cap = cfg["dual_max"] if is_dual_income else cfg["single_max"]
    return cfg["points"] if income_ratio_pct <= cap else 0


def newlywed_score(
    income_ratio_pct: float,
    is_dual_income: bool,
    residence_years: int,
    payment_count: int,
    children_count: int,
    marriage_years: float | None,
    is_single_parent: bool,
    has_child_under_2: bool,
    infants_count: int,
    table: dict[str, Any],
) -> dict[str, Any]:
    """신혼부부 특공(공공 일반형) 배점 — 순위제 + 경쟁 시 13점 만점(별표 6).

    나눔형·토지임대부(별표 6의6)는 5항목 각 3점(우선 9점/일반 12점)의 별도 체계이며,
    여기서는 가장 흔한 일반형만 계산한다(공고 유형이 다르면 공고문이 우선).
    """
    income = _income_point(income_ratio_pct, is_dual_income, table["income"])
    children = _band_score(children_count, table["children_count"])
    residence = _band_score(residence_years, table["residence_years"])
    payments = _band_score(payment_count, table["payment_count"])

    mt = table["marriage_years"]
    if marriage_years is not None:
        if marriage_years <= mt["high_max"]:
            marriage = 3
        elif marriage_years <= mt["mid_max"]:
            marriage = 2
        elif marriage_years <= mt["low_max"]:
            marriage = 1
        else:
            marriage = 0
    elif is_single_parent:
        # 한부모는 이 항목을 막내 자녀 나이로 매긴다(정확한 나이 미수집 → 근사).
        marriage = 3 if has_child_under_2 else (2 if infants_count > 0 else 1)
    else:
        marriage = 1  # 혼인기간 미상(혼인신고일 미입력) — 최소점 가정

    # 1순위: 혼인기간 중 자녀가 있는 신혼부부 또는 한부모가족.
    is_rank1 = is_single_parent or (marriage_years is not None and children_count > 0)
    total = income + children + residence + payments + marriage
    return {
        "rank": 1 if is_rank1 else 2,
        "income": income,
        "children": children,
        "residence_period": residence,
        "payment_count": payments,
        "marriage_period": marriage,
        "total": total,
        "max": 13,
    }


def newborn_score(
    income_ratio_pct: float,
    is_dual_income: bool,
    residence_years: int,
    payment_count: int,
    children_count: int,
    table: dict[str, Any],
) -> dict[str, int]:
    """신생아 특공(공공) 우선·일반공급 경쟁 배점 — 10점 만점.

    물량 배정(우선/일반/추첨)만으로 당첨이 정해지지 않고, 경쟁 시 해당지역 →
    이 배점 다득점순 → 추첨으로 선정된다(가구소득1+자녀수3+거주3+납입3).
    """
    income = _income_point(income_ratio_pct, is_dual_income, table["income"])
    children = _band_score(children_count, table["children_count"])
    residence = _band_score(residence_years, table["residence_years"])
    payments = _band_score(payment_count, table["payment_count"])
    total = income + children + residence + payments
    return {
        "income": income,
        "children": children,
        "residence_period": residence,
        "payment_count": payments,
        "total": total,
        "max": 10,
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
    # 추첨공급: 맞벌이만 상한(200%)까지 완화한다. 외벌이는 일반공급 상한(140%)을
    # 넘으면 어떤 트랙에도 해당하지 않는 부적격이다(외벌이 추첨 상한 없음).
    if is_dual_income:
        return "lottery" if income_ratio_pct <= cfg["lottery"]["income_ratio_dual_max"] else None
    return None


# --- 가구원수별 소득비율 -----------------------------------------------------


def income_ratio_pct(monthly_income_krw: int, household_size: int, rules: dict[str, Any]) -> float:
    """세전 월소득이 도시근로자 가구원수별 월평균소득의 몇 %인지 계산한다."""
    table: dict[str, int] = rules["urban_worker_monthly_income_krw"]
    size = max(1, household_size)
    if size <= 3:
        baseline = table["3"]  # 분양 소득표는 1·2·3인을 "3인 이하" 통합 행으로 적용
    elif str(size) in table:
        baseline = table[str(size)]
    else:
        baseline = table["8"] + (size - 8) * rules["extra_person_income_krw"]
    return monthly_income_krw / baseline * 100

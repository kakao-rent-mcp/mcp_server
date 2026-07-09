"""가점·배점 스코어링(scoring.py) 테스트.

수치 근거는 docs/subscription-policy-spec.md 의 검증 태그를 따른다:
- 민영 84점 가점 산식(§3.B ①②③): 🟢 청약홈 가점계산기 확인값
- 다자녀 100점 배점(§2.B.③): 🟢 (통장 가입기간은 10년 이상 5점 단일 — B-4)
- 신혼부부 배점: 공공 일반형 별표 6 순위제 + 경쟁 13점 (2026-07-06 재검증)
- 신생아 물량·소득 트랙(§2.B.①) + 경쟁 배점 10점(A-8): 2026-07-06 재검증
"""

from __future__ import annotations

import pytest

from slug_mcp import scoring
from slug_mcp.rules import load_rules

# --- 민영 §3.B.① 무주택 기간 (최대 32점) ---------------------------------


@pytest.mark.parametrize(
    ("age", "is_married", "months", "expected"),
    [
        (29, False, 36, 0),  # 만 30세 미만 미혼은 산정 대상 아님
        (29, True, 36, 8),  # 30세 미만이라도 기혼이면 산정 (3년 → 3*2+2)
        (34, False, 0, 0),  # 유주택(무주택기간 0)은 0점
        (34, False, 6, 2),  # 1년 미만 → 2점
        (34, False, 60, 12),  # 5년 → 5*2+2
        (50, False, 180, 32),  # 15년 이상 → 상한 32점
        (50, False, 240, 32),
    ],
)
def test_score_homeless_period(age, is_married, months, expected):
    assert scoring.score_homeless_period(age, is_married, months) == expected


# --- 민영 §3.B.② 부양가족 (최대 35점) ------------------------------------


@pytest.mark.parametrize(
    ("dependents", "expected"),
    [(0, 5), (1, 10), (3, 20), (6, 35), (9, 35)],
)
def test_score_dependents(dependents, expected):
    assert scoring.score_dependents(dependents) == expected


# --- 민영 §3.B.③ 통장 가입기간 본인+배우자 (합산 상한 17점) ----------------


@pytest.mark.parametrize(
    ("self_months", "spouse_months", "expected"),
    [
        (0, 0, 0),  # 통장 없음
        (3, 0, 1),  # 6개월 미만 → 1점
        (10, 0, 2),  # 6개월~1년 → 2점
        (72, 0, 8),  # 6년 → 6+2
        (180, 0, 17),  # 15년 이상 → 17점
        (72, 6, 9),  # 배우자 1년 미만 → +1
        (72, 18, 10),  # 배우자 1~2년 → +2
        (72, 36, 11),  # 배우자 2년 이상 → +3 (상한)
        (180, 36, 17),  # 합산해도 17점 상한
    ],
)
def test_score_subscription_period(self_months, spouse_months, expected):
    assert scoring.score_subscription_period(self_months, spouse_months) == expected


def test_private_general_score_max_is_84():
    result = scoring.private_general_score(
        age=50,
        is_married=True,
        homeless_duration_months=200,
        dependents_count=6,
        duration_months=200,
        spouse_duration_months=36,
    )
    assert result["total"] == 84
    assert result["homeless_period"] == 32
    assert result["dependents"] == 35
    assert result["subscription_period"] == 17


# --- 다자녀 특공 §2.B.③ (100점 만점) --------------------------------------


def test_multi_child_score_example():
    table = load_rules()["multi_child_score_table"]
    result = scoring.multi_child_score(
        children_count=2,  # 25
        infants_count=1,  # 5
        has_household_composition_bonus=True,  # +5 (한부모 또는 3세대)
        homeless_years=6,  # 15
        residence_years=11,  # 15
        account_years=6,  # 0 (10년 미만 — B-4: 5~10년 3점 구간 없음)
        table=table,
    )
    assert result["total"] == 65
    assert result["children"] == 25
    assert result["account_period"] == 0  # B-4: 10년 이상만 5점, 그 외 0점


def test_multi_child_score_maximum():
    table = load_rules()["multi_child_score_table"]
    result = scoring.multi_child_score(
        children_count=4,
        infants_count=3,
        has_household_composition_bonus=True,
        homeless_years=12,
        residence_years=12,
        account_years=12,
        table=table,
    )
    assert result["total"] == 100


# --- 신혼부부 특공 (공공 일반형: 별표 6 순위제 + 경쟁 13점) -----------------


def test_newlywed_score_general_form_13point():
    table = load_rules()["newlywed_score_table"]
    result = scoring.newlywed_score(
        income_ratio_pct=75.0,  # 외벌이 80% 이하 → 1
        is_dual_income=False,
        residence_years=3,  # 3년 이상 → 3
        payment_count=24,  # 24회 이상 → 3
        children_count=2,  # 2명 → 2
        marriage_years=2.0,  # 혼인 3년 이하 → 3
        is_single_parent=False,
        has_child_under_2=True,
        infants_count=1,
        table=table,
    )
    assert result["income"] == 1
    assert result["children"] == 2
    assert result["residence_period"] == 3
    assert result["payment_count"] == 3
    assert result["marriage_period"] == 3
    assert result["total"] == 12
    assert result["max"] == 13
    assert result["rank"] == 1  # 기혼 + 자녀 있음 → 1순위


def test_newlywed_score_low_bands_and_rank2():
    table = load_rules()["newlywed_score_table"]
    result = scoring.newlywed_score(
        income_ratio_pct=120.0,  # 80% 초과 → 0
        is_dual_income=False,
        residence_years=0,  # 1년 미만/미거주 → 1
        payment_count=3,  # 6회 미만 → 0
        children_count=0,  # 0명 → 0
        marriage_years=6.0,  # 5~7년 → 1
        is_single_parent=False,
        has_child_under_2=False,
        infants_count=0,
        table=table,
    )
    assert result["income"] == 0
    assert result["total"] == 2  # 거주1 + 혼인1
    assert result["rank"] == 2  # 자녀 없는 신혼 → 2순위


def test_newborn_score_10point():
    table = load_rules()["newborn_score_table"]
    result = scoring.newborn_score(
        income_ratio_pct=75.0,  # 외벌이 80% 이하 → 1
        is_dual_income=False,
        residence_years=3,  # 3년 이상 → 3
        payment_count=24,  # 24회 이상 → 3
        children_count=3,  # 3명 이상 → 3
        table=table,
    )
    assert result["total"] == 10
    assert result["max"] == 10


# --- 신생아 특공 소득 트랙 분기 (우선 70 / 일반 20 / 추첨 10) ---------------


@pytest.mark.parametrize(
    ("ratio", "dual", "expected"),
    [
        (95.0, False, "priority"),  # 외벌이 100% 이하
        (110.0, True, "priority"),  # 맞벌이 120% 이하
        (110.0, False, "general"),  # 외벌이 100~140%
        (145.0, True, "general"),  # 맞벌이 120~150%
        (150.0, False, None),  # 외벌이 140% 초과 → 부적격(B-8: 외벌이 추첨 상한 없음)
        (190.0, True, "lottery"),  # 맞벌이 150~200% → 추첨
        (210.0, True, None),  # 맞벌이 200% 초과 → 부적격
    ],
)
def test_newborn_track(ratio, dual, expected):
    cfg = load_rules()["newborn_supply"]
    assert scoring.newborn_track(ratio, dual, cfg) == expected


# --- 소득비율 계산 (가구원수별 도시근로자 월평균소득 대비 %) ----------------


def test_income_ratio_pct_uses_household_size_table():
    rules = load_rules()
    # 분양 소득표 "3인 이하" 통합 행 100% = 7,533,763원 (B-2: 임대 3인 8,168,429과 다름)
    ratio = scoring.income_ratio_pct(
        monthly_income_krw=7_533_763,
        household_size=3,
        rules=rules,
    )
    assert ratio == pytest.approx(100.0, abs=0.1)
    # 1·2인도 "3인 이하" 통합 행을 쓴다
    assert scoring.income_ratio_pct(7_533_763, 1, rules) == pytest.approx(100.0, abs=0.1)
    assert scoring.income_ratio_pct(7_533_763, 2, rules) == pytest.approx(100.0, abs=0.1)


def test_income_ratio_pct_household_over_8_extrapolates():
    rules = load_rules()
    base_8 = rules["urban_worker_monthly_income_krw"]["8"]
    extra = rules["extra_person_income_krw"]
    ratio = scoring.income_ratio_pct(
        monthly_income_krw=base_8 + extra,
        household_size=9,
        rules=rules,
    )
    assert ratio == pytest.approx(100.0, abs=0.1)

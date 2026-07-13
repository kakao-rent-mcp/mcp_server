"""임대 자격 판정 엔진(rental_engine) 테스트.

기준표는 config/rental_rules.yaml (마이홈포털 2026년도 적용기준,
docs/rental-policy-spec.md 참조). 판정은 일반 고시 기준 잠정판정이다.
"""

from __future__ import annotations

from slug_mcp import rental_engine
from slug_mcp.rules import load_rental_rules


def test_rental_rules_load_and_have_expected_keys():
    rules = load_rental_rules()
    # 소득표는 임대용 개별 행(1·2·3인) 체계 — 분양표("3인 이하" 통합)와 다르다.
    assert rules["rental_income_100pct_krw"]["1"] == 3813363
    assert rules["household_income_bonus_pct"]["1"] == 20
    assert rules["asset_limits_10k_won"]["national"]["total_asset"] == 34500
    for rental_type in ("permanent", "national", "happy", "public"):
        assert rental_type in rules


def test_rental_income_ratio_uses_per_size_rows_not_sale_table():
    rules = load_rental_rules()
    # 1인 가구 기본값 3,813,363원 — 분양표의 "3인 이하" 통합값(7,533,763)이 아니어야 한다.
    ratio = rental_engine.rental_income_ratio_pct(3_813_363, 1, rules)
    assert ratio is not None and round(ratio) == 100
    # 8인 이상은 고시 미확인 — None(판정 불가).
    assert rental_engine.rental_income_ratio_pct(5_000_000, 8, rules) is None


def test_income_within_cap_applies_small_household_bonus():
    rules = load_rental_rules()
    # 1인 가구 70% 기준의 실효 상한은 90% (마이홈 공표값 3,432,027원 ≈ 90%).
    # 공표값은 반올림돼 90.000008%가 되므로 경계값 대신 여유 있는 소득으로 검사한다.
    ratio = rental_engine.rental_income_ratio_pct(3_400_000, 1, rules)  # 약 89.2%
    assert rental_engine.income_within_cap(ratio, 70, 1, rules) is True
    assert rental_engine.income_within_cap(ratio, 50, 1, rules) is False  # 50%+20%p=70% 초과
    # 상한 없음(행복주택 수급자 계층 등) / 소득표 밖 → 판정 불가 None.
    assert rental_engine.income_within_cap(ratio, None, 1, rules) is None
    assert rental_engine.income_within_cap(None, 70, 1, rules) is None


def test_check_assets_blocks_over_limit_and_reports_reason():
    rules = load_rental_rules()
    # 국민임대 총자산 상한 3억 4,500만원: 부동산만으로 초과하면 확정 탈락.
    violations = rental_engine.check_assets("national", None, 400_000_000, None, rules)
    assert [v["filter"] for v in violations] == ["asset"]
    # 자동차 4,542만원 초과.
    violations = rental_engine.check_assets("national", None, None, 50_000_000, rules)
    assert [v["filter"] for v in violations] == ["vehicle"]
    # 공공임대는 총자산이 아니라 부동산 상한(2억 1,550만원)을 쓴다.
    assert rental_engine.check_assets("public", None, 300_000_000, None, rules)
    assert not rental_engine.check_assets("public", None, 200_000_000, 40_000_000, rules)


def test_check_assets_happy_uses_tier_limits():
    rules = load_rental_rules()
    # 청년 계층 총자산 2억 5,100만원 — 신혼(3억 4,500만원)보다 낮다.
    assert rental_engine.check_assets("happy", "youth", 300_000_000, None, rules)
    assert not rental_engine.check_assets("happy", "newlywed", 300_000_000, None, rules)
    # 대학생 계층은 자동차 소유 불가(vehicle: 0).
    assert rental_engine.check_assets("happy", "college_student", None, 1_000_000, rules)


def _judge_permanent(**overrides):
    kwargs = dict(
        age=40,
        income_ratio=52.0,
        household_size=1,
        is_basic_living_recipient=False,
        is_national_merit=False,
        is_near_poverty=False,
        is_single_parent=False,
        rules=load_rental_rules(),
    )
    kwargs.update(overrides)
    return rental_engine.judge_permanent(**kwargs)


def test_permanent_recipient_is_rank1():
    result = _judge_permanent(is_basic_living_recipient=True)
    assert (result["eligible"], result["rank"]) == (True, 1)
    assert "수급자" in result["basis"]


def test_permanent_income_under_50pct_is_rank2_with_bonus_note():
    # 1인 가구 소득 52% — 50% 초과지만 1인 가산(+20%p)으로 2순위 충족.
    result = _judge_permanent()
    assert (result["eligible"], result["rank"]) == (True, 2)
    assert any("공고문" in note for note in result["notes"])  # 가산 요검증 안내


def test_permanent_over_income_without_priority_is_ineligible():
    result = _judge_permanent(income_ratio=95.0)
    assert (result["eligible"], result["rank"]) == (False, None)

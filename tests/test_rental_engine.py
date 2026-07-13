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


def test_national_tiebreak_score_brackets():
    rules = load_rental_rules()
    score = rental_engine.national_tiebreak_score(
        age=52,
        dependents_count=2,
        residence_years=6,
        children_count=1,
        payment_count=70,
        rules=rules,
    )
    # 나이 50+→3, 부양 2인→2, 거주 5년+→3, 미성년 1명→0(표는 2명부터), 납입 60회+→3.
    assert (score["age"], score["dependents"], score["residence_years"]) == (3, 2, 3)
    assert (score["minor_children"], score["payment_count"]) == (0, 3)
    # 고령부양 항목은 프로필 미수집 — 0점 + 안내.
    assert score["elderly_care"] == 0
    assert score["total"] == 11
    assert any("고령" in note for note in score["notes"])


def _judge_national(**overrides):
    kwargs = dict(
        income_ratio=60.0,
        household_size=3,
        desired_size_sqm=59.0,
        account_months=30,
        payment_count=30,
        age=40,
        dependents_count=2,
        residence_years=3,
        children_count=1,
        rules=load_rental_rules(),
    )
    kwargs.update(overrides)
    return rental_engine.judge_national(**kwargs)


def test_national_income_over_cap_is_ineligible():
    # 3인 가구는 가산 없음 — 70% 초과 시 탈락.
    result = _judge_national(income_ratio=73.0)
    assert result["eligible"] is False


def test_national_50sqm_or_more_ranks_by_account():
    assert _judge_national(account_months=30, payment_count=30)["rank"] == 1  # 24개월·24회 이상
    assert _judge_national(account_months=12, payment_count=12)["rank"] == 2  # 6개월·6회 이상
    assert _judge_national(account_months=0, payment_count=0)["rank"] == 3


def test_national_under_50sqm_rank_needs_sigungu_and_says_so():
    result = _judge_national(desired_size_sqm=40.0, income_ratio=45.0)
    assert result["eligible"] is True
    assert result["rank"] is None  # 거주 시·군·구를 수집하지 않아 당해/연접 판정 불가
    assert any("거주" in note for note in result["notes"])


def test_infer_happy_tiers_priority_and_conditions():
    # 34세 기혼 5년차: 신혼부부. 미혼이면 청년. 66세면 고령자.
    assert rental_engine.infer_happy_tiers(
        age=34,
        is_married=True,
        marriage_years=5.0,
        infants_count=0,
        is_single_parent=False,
        is_housing_benefit_recipient=None,
    ) == ["newlywed"]
    assert rental_engine.infer_happy_tiers(
        age=34,
        is_married=False,
        marriage_years=None,
        infants_count=0,
        is_single_parent=False,
        is_housing_benefit_recipient=None,
    ) == ["youth"]
    tiers = rental_engine.infer_happy_tiers(
        age=66,
        is_married=False,
        marriage_years=None,
        infants_count=0,
        is_single_parent=False,
        is_housing_benefit_recipient=True,
    )
    assert tiers[0] == "welfare_recipient" and "elderly" in tiers
    # 혼인 10년차라도 6세 이하 자녀가 있으면 신혼부부 계층(OR 조건).
    assert "newlywed" in rental_engine.infer_happy_tiers(
        age=42,
        is_married=True,
        marriage_years=10.0,
        infants_count=1,
        is_single_parent=False,
        is_housing_benefit_recipient=None,
    )


def _judge_happy(**overrides):
    kwargs = dict(
        age=30,
        is_married=False,
        marriage_years=None,
        infants_count=0,
        is_single_parent=False,
        is_housing_benefit_recipient=None,
        income_ratio=90.0,
        household_size=1,
        is_dual_income=False,
        real_estate_krw=100_000_000,
        car_value_krw=None,
        rules=load_rental_rules(),
    )
    kwargs.update(overrides)
    return rental_engine.judge_happy(**kwargs)


def test_happy_youth_passes_with_household_bonus():
    # 1인 가구 청년: 소득 상한 100% + 20%p 가산 = 120%. 90%는 통과, 125%는 탈락.
    result = _judge_happy()
    assert (result["eligible"], result["tier"]) == (True, "youth")
    assert result["max_residency_years"] == 10
    assert _judge_happy(income_ratio=125.0)["eligible"] is False


def test_happy_no_matching_tier_is_ineligible_with_guidance():
    # 45세 미혼 무자녀: 어떤 계층도 아님. 대학생·산단근로자 가능성 안내.
    result = _judge_happy(age=45)
    assert result["eligible"] is False and result["tier"] is None
    assert any("대학생" in note or "산업단지" in note for note in result["notes"])


def test_happy_asset_check_uses_tier_limits():
    # 청년 총자산 상한 2억 5,100만원 초과 → 탈락.
    result = _judge_happy(real_estate_krw=260_000_000)
    assert result["eligible"] is False


def _judge_public(**overrides):
    kwargs = dict(
        income_ratio=90.0,
        household_size=4,
        desired_size_sqm=59.0,
        account_months=13,
        payment_count=13,
        target_region="경기 하남",
        rules=load_rental_rules(),
    )
    kwargs.update(overrides)
    return rental_engine.judge_public(**kwargs)


def test_public_needs_account():
    result = _judge_public(account_months=0, payment_count=0)
    assert result["eligible"] is False
    assert "통장" in result["basis"]


def test_public_rank1_differs_by_capital_region():
    # 수도권(경기)은 12개월·12회, 비수도권(부산)은 6개월·6회가 우선공급 기준.
    assert _judge_public()["rank"] == 1
    assert _judge_public(account_months=7, payment_count=7)["rank"] is None  # 수도권 미달 → 잔여
    assert _judge_public(account_months=7, payment_count=7, target_region="부산")["rank"] == 1


def test_public_income_only_applies_upto_60sqm():
    # 60㎡ 이하만 소득 100% 적용 — 초과 평형은 소득 무관.
    assert _judge_public(income_ratio=130.0)["eligible"] is False
    assert _judge_public(income_ratio=130.0, desired_size_sqm=84.0)["eligible"] is True
    # 85㎡ 초과는 공공임대(5·10년) 대상이 아니다.
    assert _judge_public(desired_size_sqm=101.0)["eligible"] is False


def _rental_doc(**overrides):
    """core가 전부 찬 영구임대 기본 문서. overrides는 최상위 키 단위로 deep-merge한다."""
    doc = {
        "target_housing": {
            "track": "rental",
            "rental_type": "permanent",
            "target_region": "성남시",
        },
        "user_profile": {
            "age": 40,
            "residence_area": "경기",
            "owned_house_count": 0,
            "income_and_assets": {"monthly_income_krw": 2_000_000},
            "welfare": {"is_basic_living_recipient": True},
        },
        "subscription_account": {},
    }
    for key, patch in overrides.items():
        base = doc.setdefault(key, {})
        for inner_key, value in patch.items():
            if isinstance(value, dict) and isinstance(base.get(inner_key), dict):
                base[inner_key].update(value)
            else:
                base[inner_key] = value
    return doc


def test_analyze_rental_needs_more_info_when_core_missing():
    result = rental_engine.analyze_rental({"target_housing": {"track": "rental"}})
    assert result["status"] == "needs_more_info"
    assert result["missing_required_fields"]


def test_analyze_rental_permanent_recipient_headline_and_notes():
    result = rental_engine.analyze_rental(_rental_doc())
    assert result["status"] == "ok"
    assert result["judgments"]["permanent"]["rank"] == 1
    assert result["eligible_types"] == ["permanent"]
    assert "1순위" in result["headline"]
    # full(자산 등) 미입력 → 잠정 판정 + 항상 공고문 대조 안내.
    assert result["confidence"] == "provisional"
    assert any("extract_lease_notice_text" in n for n in result["verification_notes"])


def test_analyze_rental_homeowner_household_is_blocked():
    result = rental_engine.analyze_rental(_rental_doc(user_profile={"owned_house_count": 1}))
    assert result["blocking"]["is_homeless_household"] is False
    assert result["eligible_types"] == []
    assert result["judgments"]["permanent"]["eligible"] is False


def test_analyze_rental_without_type_screens_all_four():
    doc = _rental_doc(target_housing={"rental_type": None})
    # 유형 미지정 core에는 rental_type이 포함되어 needs_more_info가 되므로,
    # 스크리닝 경로는 rental_type만 비운 완성 문서로 직접 검증한다.
    doc["target_housing"].pop("rental_type")
    doc["subscription_account"] = {"duration_months": 30, "payment_count": 30}
    result = rental_engine.analyze_rental(doc)
    assert result["status"] == "ok"
    assert set(result["judgments"]) == {"permanent", "national", "happy", "public"}
    assert "permanent" in result["eligible_types"]  # 수급자라 영구임대는 확실

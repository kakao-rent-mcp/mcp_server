"""판정 파이프라인(engine.py) 테스트.

스펙(docs/subscription-policy-spec.md) §2 파이프라인 전체를 검증한다:
Hard Filter(§3) → 공공/민영 분기(§4·§5) → 컷오프 대조·강제매칭(§6) → 출력(§7).
"""

from __future__ import annotations

import copy
from datetime import date

from slug_mcp import engine

AS_OF = date(2026, 7, 5)


def test_is_regulated_region_matching():
    """규제지역 매칭: 신규 지정(동탄·기흥·구리) 반영 + 구/시 구분 + 도로명 오탐 방지."""
    regulated = [
        "경기 과천",
        "경기도 과천시 별양동",
        "경기도 화성시 동탄구 오산동",  # 2026-06-29 신규
        "경기도 용인시 기흥구",  # 2026-06-29 신규
        "경기도 구리시 인창동",  # 2026-06-29 신규
        "경기도 성남시 분당구",
        "경기도 성남시 성남낙생지구 A-1블록",  # 구 생략 주소도 성남 전역이라 규제
        "경기도 수원시 팔달구",
        "서울특별시 노원구",
    ]
    not_regulated = [
        "경기도 김포시 과천봉담로 1",  # 도로명 부분일치는 규제로 오판하면 안 됨
        "경기도 화성시 효행구 비봉면",  # 화성 비(非)동탄구
        "경기도 용인시 처인구",  # 기흥·수지만 규제
        "경기도 수원시 권선구",  # 영통·장안·팔달만 규제
        "경기도 고양시 덕양구",
    ]
    for region in regulated:
        assert engine.is_regulated_region(region) is True, region
    for region in not_regulated:
        assert engine.is_regulated_region(region) is False, region


def _complete_doc(**paths: object) -> dict:
    """스펙 §1 예시와 동일한 완전한 프로필. paths로 'a.b.c'=값 오버라이드."""
    doc: dict = {
        "target_housing": {
            "target_region": "서울 마포구",
            "desired_size_sqm": 59.0,
            "is_forced_matching": False,
        },
        "user_profile": {
            "age": 34,
            "is_head_of_household": True,
            "residence_area": "서울",
            "residence_years_in_region": 4,
            "homeless_duration_months": 72,
            "owned_house_count": 0,
            "marriage": {"is_married": True, "marriage_date": "2021-03-10"},
            "children_count": 2,
            "infants_count": 1,
            "has_child_under_2": True,
            "dependents_count": 3,
            "income_and_assets": {
                "monthly_income_krw": 7_500_000,
                "is_dual_income": True,
                "total_real_estate_krw": 100_000_000,
                "car_value_krw": 30_000_000,
            },
        },
        "subscription_account": {
            "duration_months": 72,
            "payment_count": 70,
            "total_balance_krw": 18_000_000,
            "spouse_duration_months": 36,
        },
    }
    result = copy.deepcopy(doc)
    for path, value in paths.items():
        node = result
        parts = path.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return result


# --- 입력 완결성 ------------------------------------------------------------


def test_incomplete_profile_returns_needs_more_info():
    result = engine.analyze({"user_profile": {"age": 34}}, as_of=AS_OF)
    assert result["status"] == "needs_more_info"
    fields = [item["field"] for item in result["missing_required_fields"]]
    assert "subscription_account.total_balance_krw" in fields
    assert all(item["question"] for item in result["missing_required_fields"])


def test_complete_profile_returns_full_output_schema():
    result = engine.analyze(_complete_doc(), as_of=AS_OF)
    assert result["status"] == "ok"
    assert set(result) >= {
        "status",
        "eligibility_status",
        "scores",
        "matching_analysis",
        "verification_notes",
    }


# --- §3 Hard Filter ----------------------------------------------------------


def test_filter01_homeowner_blocks_public_but_not_private_general():
    """유주택 세대(주택 수≥1)는 공공 전체·민영 특공 불가. 비규제 민영 가점제는 가능(스펙 §3)."""
    result = engine.analyze(
        _complete_doc(
            **{
                "user_profile.owned_house_count": 1,
                "user_profile.homeless_duration_months": 0,
                "user_profile.residence_area": "부산",
                "target_housing.target_region": "부산",
            }
        ),
        as_of=AS_OF,
    )
    status = result["eligibility_status"]
    assert status["is_eligible_for_public"] is False
    assert status["is_eligible_for_private"] is True  # 배제가 아니라 무주택 가점 0점
    assert result["scores"]["private_score_breakdown"]["homeless_period"] == 0
    assert result["scores"]["special_supply_scores"]["newborn"] is None  # 특공 차단
    assert any("Filter-01" in r["filter"] for r in status["disqualification_reasons"])


def test_filter02_asset_cutoff_applies_only_under_60sqm():
    over_asset = {"user_profile.income_and_assets.total_real_estate_krw": 300_000_000}
    # 전용 59㎡ 신청 → 자산 컷 적용
    small = engine.analyze(_complete_doc(**over_asset), as_of=AS_OF)
    assert small["eligibility_status"]["is_eligible_for_public"] is False
    assert any(
        "Filter-02" in r["filter"] for r in small["eligibility_status"]["disqualification_reasons"]
    )
    # 전용 84㎡ 신청 → 60㎡ 이하 자산 컷 미적용
    large = engine.analyze(
        _complete_doc(**over_asset, **{"target_housing.desired_size_sqm": 84.0}), as_of=AS_OF
    )
    assert large["eligibility_status"]["is_eligible_for_public"] is True


def test_filter02_car_value_cutoff():
    result = engine.analyze(
        _complete_doc(**{"user_profile.income_and_assets.car_value_krw": 46_000_000}),
        as_of=AS_OF,
    )
    assert result["eligibility_status"]["is_eligible_for_public"] is False


def test_filter03_regulated_region_requires_head_of_household():
    result = engine.analyze(
        _complete_doc(**{"user_profile.is_head_of_household": False}), as_of=AS_OF
    )
    status = result["eligibility_status"]
    # 규제지역(서울) 1순위 불가 — 공공 일반 1순위·민영 1순위 모두
    assert status["is_eligible_for_public"] is False
    assert status["is_eligible_for_private_rank1"] is False
    assert any("Filter-03" in r["filter"] for r in status["disqualification_reasons"])


def test_filter03_not_applied_outside_regulated_regions():
    result = engine.analyze(
        _complete_doc(
            **{
                "user_profile.is_head_of_household": False,
                "user_profile.residence_area": "부산",
                "target_housing.target_region": "부산",
            }
        ),
        as_of=AS_OF,
    )
    assert not any(
        "Filter-03" in r["filter"] for r in result["eligibility_status"]["disqualification_reasons"]
    )


def test_regulated_region_detection_uses_dynamic_config():
    """'강남3구·용산' 하드코딩 금지 — 서울 전역+경기 15곳(2026-07-01 추가 반영) 목록으로 판정."""
    assert engine.is_regulated_region("서울 노원구") is True  # 서울 전역
    assert engine.is_regulated_region("경기 하남") is True  # 경기 15곳
    assert engine.is_regulated_region("성남 분당") is True
    assert engine.is_regulated_region("화성 동탄") is True  # 2026-07-01 추가 (B-1)
    assert engine.is_regulated_region("구리") is True  # 2026-07-01 추가 (B-1)
    assert engine.is_regulated_region("경기 평택") is False
    assert engine.is_regulated_region("부산") is False


# --- §4 공공분양 분기 ---------------------------------------------------------


def test_public_recognized_balance_applies_25man_cap_with_retroactive_10man():
    """월 인정 상한: 2024-11-01 전 회차는 10만원, 이후는 25만원(부칙 소급 — B-10).

    가입 72개월·납입 70회, as_of=2026-07-05 → 시행 전 52개월분 10만, 이후 18회 25만
    → 52×10만 + 18×25만 = 9,700,000원.
    """
    result = engine.analyze(_complete_doc(), as_of=AS_OF)
    assert result["scores"]["public_balance_recognized_krw"] == 9_700_000


def test_public_rank1_in_regulated_region_uses_overheated_requirement():
    """규제지역은 가입 24개월·납입 24회 요건을 적용한다."""
    result = engine.analyze(
        _complete_doc(
            **{
                "subscription_account.duration_months": 18,
                "subscription_account.payment_count": 18,
            }
        ),
        as_of=AS_OF,
    )
    assert result["eligibility_status"]["is_eligible_for_public"] is False
    reasons = str(result["eligibility_status"]["disqualification_reasons"])
    assert "24" in reasons


# --- §2.B 특별공급 매칭·배점 --------------------------------------------------


def test_newborn_special_supply_priority_track():
    """4인 가구 맞벌이 월 750만원 → 소득 85.2% → 우선공급(70%) 트랙."""
    result = engine.analyze(_complete_doc(), as_of=AS_OF)
    newborn = result["scores"]["special_supply_scores"]["newborn"]
    assert newborn["track"] == "priority"
    assert newborn["track_share_pct"] == 70


def test_newborn_requires_child_under_2():
    result = engine.analyze(_complete_doc(**{"user_profile.has_child_under_2": False}), as_of=AS_OF)
    assert result["scores"]["special_supply_scores"]["newborn"] is None


def test_newlywed_scored_within_7_years():
    """일반형(별표 6) 13점: 소득1 + 자녀2(→2) + 거주4년(→3) + 납입70회(→3) + 혼인5.3년(→1) = 10."""
    result = engine.analyze(_complete_doc(), as_of=AS_OF)
    newlywed = result["scores"]["special_supply_scores"]["newlywed"]
    assert newlywed["total"] == 10
    assert newlywed["max"] == 13
    assert newlywed["rank"] == 1  # 혼인 중 자녀 있음 → 1순위


def test_newlywed_excluded_after_7_years_without_young_child():
    """혼인 7년 초과 + 6세 이하 자녀 없음 → 신혼 특공 자격 없음(6세 이하 자녀 있으면 B-6로 자격)."""
    result = engine.analyze(
        _complete_doc(
            **{
                "user_profile.marriage.marriage_date": "2015-01-01",
                "user_profile.has_child_under_2": False,
                "user_profile.infants_count": 0,
                "user_profile.children_count": 0,
            }
        ),
        as_of=AS_OF,
    )
    assert result["scores"]["special_supply_scores"]["newlywed"] is None


def test_newlywed_eligible_over_7_years_with_child_under_6():
    """혼인 7년 초과라도 6세 이하 자녀가 있으면 신혼 특공 자격(B-6)."""
    result = engine.analyze(
        _complete_doc(**{"user_profile.marriage.marriage_date": "2015-01-01"}), as_of=AS_OF
    )
    assert result["scores"]["special_supply_scores"]["newlywed"] is not None


def test_multi_child_score_matches_table():
    result = engine.analyze(_complete_doc(), as_of=AS_OF)
    multi = result["scores"]["special_supply_scores"]["multi_child"]
    # 자녀2(25) + 영유아1(5) + 무주택6년(15) + 거주4년(5) + 통장6년(0, 10년 미만) = 50
    assert multi["total"] == 50
    assert multi["account_period"] == 0  # B-4: 10년 미만은 0점(5~10년 3점 구간 제거)


def test_multi_child_requires_two_children():
    result = engine.analyze(_complete_doc(**{"user_profile.children_count": 1}), as_of=AS_OF)
    assert result["scores"]["special_supply_scores"]["multi_child"] is None


# --- §5 민영주택 분기 ---------------------------------------------------------


def test_private_general_score_matches_spec_example():
    """무주택기간 30세/혼인 기산 상한(C-1) 적용 → 가점 43점 (무주택12 + 부양20 + 통장11).

    혼인 2021-03(as_of 2026-07 기준 5.3년) 기산 → 무주택 인정 63개월=5년 → 12점(입력 72개월 아님).
    """
    result = engine.analyze(_complete_doc(), as_of=AS_OF)
    assert result["scores"]["private_general_score"] == 43
    assert result["scores"]["private_score_breakdown"]["homeless_period"] == 12


def test_private_rank1_deposit_shortfall():
    """서울 59㎡ 예치금 기준 300만원 미달이면 민영 1순위 박탈(감점 아님)."""
    result = engine.analyze(
        _complete_doc(**{"subscription_account.total_balance_krw": 2_000_000}), as_of=AS_OF
    )
    assert result["eligibility_status"]["is_eligible_for_private_rank1"] is False
    assert result["scores"]["private_general_score"] > 0  # 가점 자체는 계산된다


# --- §6 컷오프 대조·강제매칭 --------------------------------------------------


def test_matching_analysis_grades_target_region():
    result = engine.analyze(_complete_doc(), as_of=AS_OF)
    matching = result["matching_analysis"]
    assert matching["region_grade"] == "S"  # 마포 → S
    assert matching["is_regulated_region"] is True
    # 가점 45점 vs S급 컷 69~74 → 매우낮음
    assert "매우낮음" in matching["feasibility_by_track"]["private_general"]


def test_recommended_tracks_prioritize_newborn_when_eligible():
    """2세 미만 자녀가 있으면 신생아 특공이 최우선 추천(§6.B Case 3)."""
    result = engine.analyze(_complete_doc(), as_of=AS_OF)
    tracks = result["matching_analysis"]["recommended_tracks"]
    assert tracks, "추천 트랙이 비어 있으면 안 된다"
    assert "신생아" in tracks[0]["type"]


def test_forced_matching_provides_gap_and_alternatives():
    result = engine.analyze(
        _complete_doc(**{"target_housing.is_forced_matching": True}), as_of=AS_OF
    )
    forced = result["matching_analysis"]["forced_matching"]
    assert forced is not None
    assert forced["private_score_gap"] == 69 - 43
    assert result["matching_analysis"]["alternatives"], "S급 미달이면 대안 지역을 제시해야 한다"


def test_high_score_in_grade_c_region_is_feasible():
    result = engine.analyze(
        _complete_doc(
            **{
                "user_profile.residence_area": "전북",
                "target_housing.target_region": "전북 전주",
                "user_profile.is_head_of_household": True,
            }
        ),
        as_of=AS_OF,
    )
    matching = result["matching_analysis"]
    assert matching["region_grade"] == "C"
    assert matching["is_regulated_region"] is False
    assert "매우높음" in matching["feasibility_by_track"]["private_general"]


# --- 검증 주의(verification_notes) --------------------------------------------


def test_verification_notes_flag_yellow_rules():
    """🟡 규칙(신혼 배점표·자산 컷·월 25만 상한)이 판정에 관여하면 경고를 남긴다."""
    result = engine.analyze(_complete_doc(), as_of=AS_OF)
    notes = " ".join(result["verification_notes"])
    assert "신혼부부" in notes
    assert "25만" in notes


def test_marriage_date_missing_still_scores_newlywed_with_note():
    result = engine.analyze(
        _complete_doc(**{"user_profile.marriage.marriage_date": None}), as_of=AS_OF
    )
    assert result["scores"]["special_supply_scores"]["newlywed"] is not None
    assert any("혼인" in note for note in result["verification_notes"])


# --- 2026-07-06 정합성 수정: 주택수·통장유형·소득·예치금·가입기간·자산범위 게이트 ---


def test_owned_house_count_is_required():
    doc = _complete_doc()
    del doc["user_profile"]["owned_house_count"]
    result = engine.analyze(doc, as_of=AS_OF)
    assert result["status"] == "needs_more_info"
    assert any(
        item["field"] == "user_profile.owned_house_count"
        for item in result["missing_required_fields"]
    )


def test_two_house_household_blocked_from_regulated_rank1():
    """규제지역 2주택 이상 세대는 1순위 배제 (C-6)."""
    result = engine.analyze(_complete_doc(**{"user_profile.owned_house_count": 2}), as_of=AS_OF)
    status = result["eligibility_status"]
    assert status["is_eligible_for_private_rank1"] is False
    assert any("2주택" in r["filter"] for r in status["disqualification_reasons"])


def test_account_type_public_savings_blocks_private():
    """청약저축은 국민(공공)주택만 → 민영 트랙 제외 (C-5)."""
    result = engine.analyze(
        _complete_doc(**{"subscription_account.account_type": "public_savings"}), as_of=AS_OF
    )
    assert result["eligibility_status"]["is_eligible_for_private"] is False


def test_account_type_private_deposit_blocks_public():
    """청약예금은 민영주택만 → 공공·특공 트랙 제외 (C-5)."""
    result = engine.analyze(
        _complete_doc(**{"subscription_account.account_type": "private_deposit"}), as_of=AS_OF
    )
    assert result["eligibility_status"]["is_eligible_for_public"] is False
    assert result["scores"]["special_supply_scores"]["newborn"] is None


def test_special_supply_requires_subscription_account():
    """청약통장이 없으면 특별공급 배점을 계산하지 않는다 (A-9)."""
    result = engine.analyze(
        _complete_doc(
            **{
                "subscription_account.duration_months": 0,
                "subscription_account.payment_count": 0,
            }
        ),
        as_of=AS_OF,
    )
    scores = result["scores"]["special_supply_scores"]
    assert scores["newborn"] is None
    assert scores["newlywed"] is None
    assert scores["multi_child"] is None


def test_newlywed_income_cap_excludes_high_earner():
    """소득 상한 초과 외벌이는 신혼 특공 대상 아님 (A-2)."""
    result = engine.analyze(
        _complete_doc(
            **{
                "user_profile.income_and_assets.monthly_income_krw": 15_000_000,
                "user_profile.income_and_assets.is_dual_income": False,
            }
        ),
        as_of=AS_OF,
    )
    assert result["scores"]["special_supply_scores"]["newlywed"] is None


def test_asset_excess_blocks_special_supply_regardless_of_size():
    """자산 초과는 84㎡ 특공에도 적용된다 (B-9: 특공 전체 면적무관)."""
    result = engine.analyze(
        _complete_doc(
            **{
                "target_housing.desired_size_sqm": 84.0,
                "user_profile.income_and_assets.total_real_estate_krw": 300_000_000,
            }
        ),
        as_of=AS_OF,
    )
    scores = result["scores"]["special_supply_scores"]
    assert scores["newborn"] is None
    assert scores["newlywed"] is None
    assert scores["multi_child"] is None


def test_sejong_deposit_uses_other_region_tier():
    """세종은 기타지역(85㎡↓ 200만원) — 광역시(250만원)로 취급하면 안 됨 (B-11)."""
    result = engine.analyze(
        _complete_doc(
            **{
                "user_profile.residence_area": "세종",
                "target_housing.target_region": "세종",
                "subscription_account.total_balance_krw": 2_200_000,
            }
        ),
        as_of=AS_OF,
    )
    # 200만원 기준 충족(2.2백만) → 1순위. tier2(250만)였다면 미달로 박탈될 값.
    assert result["eligibility_status"]["is_eligible_for_private_rank1"] is True


def test_private_rank1_requires_subscription_duration():
    """민영 1순위는 예치금 외 가입기간도 필요 (A-1). 규제지역 24개월 미달 → 2순위."""
    result = engine.analyze(
        _complete_doc(**{"subscription_account.duration_months": 12}), as_of=AS_OF
    )
    status = result["eligibility_status"]
    assert status["is_eligible_for_private_rank1"] is False
    assert any("가입" in r["reason"] for r in status["disqualification_reasons"])

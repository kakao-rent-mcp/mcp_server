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
    """유주택자는 공공 전체·민영 특공 불가. 민영 일반공급(가점제)은 가능(스펙 §3 주의)."""
    result = engine.analyze(
        _complete_doc(**{"user_profile.homeless_duration_months": 0}), as_of=AS_OF
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
    """'강남3구·용산' 하드코딩 금지 — 10.15 대책의 서울 전역+경기 12곳 목록으로 판정."""
    assert engine.is_regulated_region("서울 노원구") is True  # 서울 전역
    assert engine.is_regulated_region("경기 하남") is True  # 경기 12곳
    assert engine.is_regulated_region("성남 분당") is True
    assert engine.is_regulated_region("경기 평택") is False
    assert engine.is_regulated_region("부산") is False


# --- §4 공공분양 분기 ---------------------------------------------------------


def test_public_recognized_balance_caps_monthly_payment():
    """월 25만원 인정 상한: 납입횟수 70회면 인정총액은 최대 1,750만원."""
    result = engine.analyze(_complete_doc(), as_of=AS_OF)
    assert result["scores"]["public_balance_recognized_krw"] == 17_500_000


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
    result = engine.analyze(_complete_doc(), as_of=AS_OF)
    newlywed = result["scores"]["special_supply_scores"]["newlywed"]
    # 소득 85.2%(→2) + 거주 4년(→3) + 납입 70회(→3) = 우선공급 경쟁 8점
    assert newlywed["priority_total"] == 8
    # 거주3 + 납입3 + 자녀2명(→2) + 무주택 6년(→3) = 일반공급 경쟁 11점
    assert newlywed["general_total"] == 11


def test_newlywed_excluded_after_7_years():
    result = engine.analyze(
        _complete_doc(**{"user_profile.marriage.marriage_date": "2015-01-01"}), as_of=AS_OF
    )
    assert result["scores"]["special_supply_scores"]["newlywed"] is None


def test_multi_child_score_matches_table():
    result = engine.analyze(_complete_doc(), as_of=AS_OF)
    multi = result["scores"]["special_supply_scores"]["multi_child"]
    # 자녀2(25) + 영유아1(5) + 무주택6년(15) + 거주4년(5) + 통장6년(3) = 53
    assert multi["total"] == 53


def test_multi_child_requires_two_children():
    result = engine.analyze(_complete_doc(**{"user_profile.children_count": 1}), as_of=AS_OF)
    assert result["scores"]["special_supply_scores"]["multi_child"] is None


# --- §5 민영주택 분기 ---------------------------------------------------------


def test_private_general_score_matches_spec_example():
    """스펙 §7 예시와 동일 프로필 → 가점 45점 (무주택14 + 부양20 + 통장11)."""
    result = engine.analyze(_complete_doc(), as_of=AS_OF)
    assert result["scores"]["private_general_score"] == 45


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
    assert forced["private_score_gap"] == 69 - 45
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

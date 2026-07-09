"""청약 자격판정·매칭 파이프라인 (스펙 §2 전체 흐름의 구현).

[입력 문서] → 1단계 Hard Filter(§3) → 2단계 공공/민영 분기(§4·§5)
           → 3단계 가점·배점 연산 → 4단계 컷오프 대조·강제매칭(§6) → 출력(§7)

순수 계산만 하며 네트워크 호출이 없다. 기준값은 config/eligibility_rules.yaml,
정책 근거·검증 상태는 docs/subscription-policy-spec.md 를 따른다.
🔴(미검증) 규칙 — 미성년자 통장 인정한도, 민영 신생아특공 물량 — 은 스펙 지침대로
로직에 반영하지 않는다.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from . import scoring
from .models import ProfileDocument, missing_fields
from .rules import load_rules

_CAPITAL_SIDO_TOKENS = ("서울", "경기", "인천")

_NEWLYWED_MAX_MARRIAGE_YEARS = 7


def _mentions_admin_unit(region: str, name: str) -> bool:
    """region 안에 name이 행정구역(시/군/구/읍/면/동) 단위로 등장하는지 판정한다.

    규제 목록은 모두 시/구 단위이므로, name 뒤가 '시/군/구'이거나 공백·문자열 끝일 때만
    매칭한다. 도로명(예: '김포시 과천봉담로'의 '과천')·리/동명 부분일치는 배제된다.
    """
    return re.search(rf"{re.escape(name)}(?:시|군|구|\s|$)", region) is not None


def is_regulated_region(region: str | None) -> bool:
    """지역 문자열이 규제지역(조정대상지역·투기과열지구)에 속하는지 판정한다.

    목록은 yaml의 regulated_regions만 보며 하드코딩하지 않는다. district 항목이 여러
    토큰이면(예: '성남 분당') 모두 행정단위로 등장해야 매칭된다('성남시 분당구').
    """
    if not region:
        return False
    rules = load_rules()["regulated_regions"]
    for sido in rules["full_sido"]:
        if sido in region:
            return True
    for district in rules["gyeonggi_districts"]:
        if all(_mentions_admin_unit(region, token) for token in district.split()):
            return True
    return False


def region_grade(region: str | None) -> str:
    """지역 문자열을 컷오프 등급(S/A/B/C)으로 매핑한다. 미매칭은 C."""
    if not region:
        return "C"
    keywords: dict[str, list[str]] = load_rules()["region_grade_keywords"]
    for grade in ("S", "A", "B"):
        for keyword in keywords.get(grade, []):
            if keyword in region:
                return grade
    return "C"


def _region_tier(region: str) -> str:
    tiers = load_rules()["region_tier"]
    for tier_name in ("tier1", "tier2"):
        for sido in tiers.get(tier_name, []):
            if sido in region:
                return tier_name
    return "tier3"


def _deposit_area_bracket(area_sqm: float) -> str:
    if area_sqm <= 85:
        return "85"
    if area_sqm <= 102:
        return "102"
    if area_sqm <= 135:
        return "135"
    return "all"


def feasibility_label(probability_pct: int) -> str:
    # 프로필 점수와 지역 등급으로만 매긴 '추정' 등급이다. 특정 확률(%)이 아니라
    # 개별 공고 경쟁률과도 무관하므로 확률로 표기하지 않는다.
    label = {80: "매우높음", 60: "높음", 40: "보통", 20: "낮음", 5: "매우낮음"}[probability_pct]
    return f"{label}(추정)"


def private_feasibility_pct(score: int, cutoff_min: int, cutoff_max: int) -> int:
    if score > cutoff_max:
        return 80
    if score >= (cutoff_min + cutoff_max) / 2:
        return 60
    if score >= cutoff_min:
        return 40
    if score >= cutoff_min - 10:
        return 20
    return 5


def public_feasibility_pct(recognized_krw: int, cutoff_min_krw: int) -> int:
    if recognized_krw >= cutoff_min_krw * 1.2:
        return 80
    if recognized_krw >= cutoff_min_krw:
        return 60
    if recognized_krw >= cutoff_min_krw * 0.8:
        return 40
    if recognized_krw >= cutoff_min_krw * 0.6:
        return 20
    return 5


def _years_since(date_str: str, as_of: date) -> float:
    parsed = date.fromisoformat(date_str)
    return (as_of - parsed).days / 365.25


def analyze(doc: dict[str, Any], as_of: date | None = None) -> dict[str, Any]:
    """프로필 문서를 판정해 스펙 §7 출력 스키마를 돌려준다.

    필수 항목이 비어 있으면 status="needs_more_info"와 함께 클라이언트 AI가
    사용자에게 물어볼 질문 목록을 돌려준다.
    """
    as_of = as_of or date.today()
    required_missing, optional_missing = missing_fields(doc)
    if required_missing:
        return {
            "status": "needs_more_info",
            "missing_required_fields": required_missing,
            "missing_optional_fields": optional_missing,
            "guidance": "필수 항목을 채운 뒤 다시 분석하세요. update_my_profile로 "
            "부분 업데이트할 수 있습니다.",
        }

    profile_doc = ProfileDocument.model_validate(doc)
    user = profile_doc.user_profile
    account = profile_doc.subscription_account
    target = profile_doc.target_housing
    rules = load_rules()

    notes: list[str] = []
    disqualifications: list[dict[str, Any]] = []

    # 필수 검증을 통과했으므로 핵심 필드는 None이 아니다 (mypy·가독성용 지역변수).
    age = user.age or 0
    homeless_months = user.homeless_duration_months or 0
    dependents = user.dependents_count or 0
    monthly_income = user.income_and_assets.monthly_income_krw or 0
    duration_months = account.duration_months or 0
    balance_krw = account.total_balance_krw or 0
    residence_area = user.residence_area or ""
    target_region = target.target_region or residence_area

    is_married = bool(user.marriage.is_married)
    children = user.children_count or 0
    infants = user.infants_count or 0
    household_size = dependents + 1  # 부양가족(본인 제외) + 본인
    income_ratio = scoring.income_ratio_pct(monthly_income, household_size, rules)
    homeless_years = homeless_months // 12
    residence_years = user.residence_years_in_region or 0
    account_years = duration_months // 12
    regulated = is_regulated_region(target_region)
    desired_size = target.desired_size_sqm

    # ---- 1단계 Hard Filter (§3) ------------------------------------------
    # 민영 특공은 미구현(스펙 §5.C 🔴 미검증)이라 별도 차단 플래그 없이
    # public_blocked가 공공 트랙 전체(일반+특공)를 함께 가른다.
    public_blocked = False
    private_rank1_blocked = False

    # Filter-01 · 무주택 요건. 민영 일반공급은 배제하지 않는다(가점만 하락).
    if homeless_months == 0:
        public_blocked = True
        disqualifications.append(
            {
                "filter": "Filter-01(무주택)",
                "reason": "유주택 세대는 공공분양 전체와 민영 특별공급에 신청할 수 없습니다. "
                "민영 일반공급(가점제)은 가능하지만 무주택기간 가점이 0점입니다.",
                "blocked_tracks": ["public_all", "private_special"],
            }
        )

    # Filter-02 · 공공분양 자산 컷 (전용 60㎡ 이하 신청 시)
    asset_limits = rules["asset_limits"]
    real_estate = user.income_and_assets.total_real_estate_krw
    car_value = user.income_and_assets.car_value_krw
    if desired_size is not None and desired_size <= 60:
        real_estate_cap = asset_limits["real_estate_10k_won"] * 10_000
        car_cap = asset_limits["vehicle_10k_won"] * 10_000
        if real_estate is not None and real_estate > real_estate_cap:
            public_blocked = True
            disqualifications.append(
                {
                    "filter": "Filter-02(자산)",
                    "reason": f"부동산 자산({real_estate:,}원)이 공공분양 60㎡ 이하 기준"
                    f"({real_estate_cap:,}원)을 초과합니다.",
                    "blocked_tracks": ["public_under_60sqm"],
                }
            )
        if car_value is not None and car_value > car_cap:
            public_blocked = True
            disqualifications.append(
                {
                    "filter": "Filter-02(자산)",
                    "reason": f"자동차 가액({car_value:,}원)이 기준({car_cap:,}원)을 초과합니다.",
                    "blocked_tracks": ["public_under_60sqm"],
                }
            )
        notes.append(
            "공공분양 자산 상한(부동산 2.155억/자동차 4,542만원)은 2026-02-27 공고분 기준 — "
            "신청 전 공고 원문의 자산보유 기준 표로 재확인하세요(🟡)."
        )
        if real_estate is None or car_value is None:
            notes.append(
                "부동산·자동차 자산이 미입력이라 공공분양 자산 기준을 완전히 확인하지 못했습니다."
            )
    elif desired_size is None:
        notes.append(
            "희망 전용면적 미입력 — 60㎡ 이하 자산 컷은 건너뛰고 예치금은 85㎡ 기준으로 "
            "판정했습니다."
        )

    # Filter-03 · 규제지역 1순위 세대주 요건 (동적 규제지역 목록)
    if regulated:
        notes.append("규제지역 판정은 2025-10-15 대책(서울 전역+경기 12곳) 목록 기준입니다.")
        if user.is_head_of_household is False:
            public_blocked = True
            private_rank1_blocked = True
            disqualifications.append(
                {
                    "filter": "Filter-03(규제지역 세대주)",
                    "reason": f"목표지역({target_region})은 규제지역이라 세대주만 1순위 "
                    "신청이 가능합니다. 세대원은 민영 1순위·공공 일반공급 1순위에 진입할 "
                    "수 없습니다.",
                    "blocked_tracks": ["public_general_rank1", "private_rank1"],
                }
            )
        elif user.is_head_of_household is None:
            notes.append("세대주 여부 미입력 — 규제지역 1순위 요건을 확인하지 못했습니다.")

    # ---- 2단계 [BRANCH A] 공공분양 (§4) ------------------------------------
    rank1_cfg = rules["subscription_account"]["public"]["rank1_requirement"]
    if regulated:
        rank1_req = rank1_cfg["overheated_region"]
    elif any(token in target_region for token in _CAPITAL_SIDO_TOKENS):
        rank1_req = rank1_cfg["capital_region"]
    else:
        rank1_req = rank1_cfg["non_capital_region"]

    payments = account.payment_count
    public_rank1_ok = (
        duration_months >= rank1_req["min_months"] and (payments or 0) >= rank1_req["min_payments"]
    )
    if not public_rank1_ok and not public_blocked:
        disqualifications.append(
            {
                "filter": "Rank1(공공 1순위)",
                "reason": f"공공 1순위는 가입 {rank1_req['min_months']}개월·납입 "
                f"{rank1_req['min_payments']}회 이상이 필요합니다 "
                f"(현재 {duration_months}개월·{payments or 0}회).",
                "blocked_tracks": ["public_general_rank1"],
            }
        )

    monthly_cap = rules["subscription_account"]["public"]["max_recognized_monthly_payment_krw"]
    if payments is not None:
        recognized_krw = min(balance_krw, payments * monthly_cap)
        notes.append(
            "공공 인정총액은 월 25만원 납입 상한(2024-11 개정)을 가정해 계산했습니다 — "
            "개정 전 납입분 소급 조건은 공고 원문 확인이 필요합니다(🟡)."
        )
    else:
        recognized_krw = balance_krw
        notes.append("납입횟수 미입력 — 공공 인정총액을 납입총액 그대로 사용했습니다.")
    if desired_size is not None and desired_size <= 40:
        notes.append("전용 40㎡ 이하는 저축총액이 아닌 납입횟수 순으로 선정됩니다(§4.A).")

    is_eligible_for_public = not public_blocked and public_rank1_ok

    # ---- 특별공급 매칭·배점 (§2.B) — 전부 공공분양 트랙 ---------------------
    special_scores: dict[str, Any] = {"newborn": None, "newlywed": None, "multi_child": None}

    if not public_blocked:
        # ① 신생아 특별공급 (혼인 무관, 2세 미만 자녀)
        if user.has_child_under_2:
            track = scoring.newborn_track(
                income_ratio, user.income_and_assets.is_dual_income, rules["newborn_supply"]
            )
            if track is not None:
                special_scores["newborn"] = {
                    "track": track,
                    "track_share_pct": rules["newborn_supply"][track]["share"],
                    "income_ratio_pct": round(income_ratio, 1),
                }
                notes.append(
                    "신생아 특공 소득 구간(우선100/일반140/추첨, 맞벌이 완화)은 공고별로 다를 수 "
                    "있어 공고 원문 확인이 필요합니다(🟡)."
                )
            else:
                notes.append(
                    f"신생아 특공: 소득비율 {income_ratio:.0f}%가 맞벌이 상한(200%)을 초과해 "
                    "대상이 아닙니다."
                )

        # ② 신혼부부 특별공급 (혼인 7년 이내)
        newlywed_eligible = False
        if is_married:
            if user.marriage.marriage_date:
                newlywed_eligible = (
                    _years_since(user.marriage.marriage_date, as_of) <= _NEWLYWED_MAX_MARRIAGE_YEARS
                )
            else:
                newlywed_eligible = True
                notes.append(
                    "혼인신고일 미입력 — 신혼부부 특공의 '혼인 7년 이내' 요건을 확인하지 "
                    "못했습니다."
                )
        if newlywed_eligible:
            special_scores["newlywed"] = scoring.newlywed_score(
                income_ratio_pct=income_ratio,
                residence_years=residence_years,
                payment_count=payments or 0,
                children_count=children,
                homeless_years=homeless_years,
                table=rules["newlywed_score_table"],
            )
            notes.append(
                "신혼부부 특공 배점표는 LH청약플러스 2026-07-05 확인값(우선공급 경쟁 9점/"
                "일반공급 경쟁 12점)입니다 — 개별 공고문과 다르면 공고문이 우선합니다."
            )

        # ③ 다자녀 특별공급 (미성년 자녀 2명 이상)
        if children >= 2:
            special_scores["multi_child"] = scoring.multi_child_score(
                children_count=children,
                infants_count=infants,
                has_household_composition_bonus=(
                    user.is_three_generation_household or user.is_single_parent
                ),
                homeless_years=homeless_years,
                residence_years=residence_years,
                account_years=account_years,
                table=rules["multi_child_score_table"],
            )

    # ---- 2단계 [BRANCH B] 민영주택 (§5) ------------------------------------
    private_breakdown = scoring.private_general_score(
        age=age,
        is_married=is_married,
        homeless_duration_months=homeless_months,
        dependents_count=dependents,
        duration_months=duration_months,
        spouse_duration_months=account.spouse_duration_months,
    )

    deposit_table = rules["subscription_account"]["private"]["deposit_table_10k_won"]
    bracket = _deposit_area_bracket(desired_size if desired_size is not None else 85)
    required_deposit_krw = deposit_table[bracket][_region_tier(residence_area)] * 10_000
    deposit_ok = balance_krw >= required_deposit_krw
    if not deposit_ok:
        disqualifications.append(
            {
                "filter": "Rank1(민영 예치금)",
                "reason": f"예치금 {balance_krw:,}원이 {residence_area}·전용 {bracket}㎡ 이하 "
                f"기준({required_deposit_krw:,}원)에 못 미쳐 민영 1순위 자격이 없습니다"
                "(감점이 아니라 박탈).",
                "blocked_tracks": ["private_rank1"],
            }
        )

    has_account = duration_months > 0
    is_eligible_for_private = has_account
    if not has_account:
        disqualifications.append(
            {
                "filter": "Rank1(민영 통장)",
                "reason": "청약통장이 없어 민영주택 순위 신청이 불가합니다.",
                "blocked_tracks": ["private_all"],
            }
        )
    is_eligible_for_private_rank1 = has_account and deposit_ok and not private_rank1_blocked

    # ---- 4단계 컷오프 대조 + 강제 매칭 (§6) --------------------------------
    grade = region_grade(target_region)
    cutoffs = rules["expected_cutoffs"][grade]
    private_score: int = private_breakdown["total"]
    private_pct = private_feasibility_pct(
        private_score, cutoffs["private_score_min"], cutoffs["private_score_max"]
    )
    public_pct = public_feasibility_pct(recognized_krw, cutoffs["public_balance_min_krw"])

    feasibility_by_track = {
        "private_general": feasibility_label(private_pct if is_eligible_for_private else 5),
        "public_general": feasibility_label(public_pct if is_eligible_for_public else 5),
    }
    best_pct = max(
        private_pct if is_eligible_for_private else 5,
        public_pct if is_eligible_for_public else 5,
    )

    recommended_tracks: list[dict[str, str]] = []
    newborn = special_scores["newborn"]
    if newborn is not None:
        track_names = {
            "priority": "우선공급(70%)",
            "general": "일반공급(20%)",
            "lottery": "추첨공급(10%)",
        }
        recommended_tracks.append(
            {
                "type": f"신생아 특별공급(공공) {track_names[newborn['track']]}",
                "reason": f"2세 미만 자녀가 있고 소득비율 {newborn['income_ratio_pct']}%로 "
                f"{track_names[newborn['track']]} 트랙에 해당합니다. 목표지역 1순위 "
                "타깃으로 추천합니다.",
            }
        )
    multi = special_scores["multi_child"]
    if multi is not None:
        recommended_tracks.append(
            {
                "type": "다자녀 특별공급(공공)",
                "reason": f"배점 {multi['total']}점/100점. 미성년 자녀 {children}명으로 "
                "자격이 됩니다.",
            }
        )
    newlywed = special_scores["newlywed"]
    if newlywed is not None:
        recommended_tracks.append(
            {
                "type": "신혼부부 특별공급(공공)",
                "reason": f"우선공급 경쟁 {newlywed['priority_total']}/9점, "
                f"일반공급 경쟁 {newlywed['general_total']}/12점.",
            }
        )
    first_time_cap = rules["income_ratio_by_supply_type"]["first_time"]
    if (
        not public_blocked
        and (is_married or children > 0)
        and homeless_months > 0
        and income_ratio <= first_time_cap
    ):
        recommended_tracks.append(
            {
                "type": "생애최초 특별공급",
                "reason": f"무주택·소득요건(≤{first_time_cap}%) 충족 시 가점 무관 추첨 트랙입니다. "
                "생애 최초 주택 구입 여부 등 세부 요건은 공고문으로 확인하세요.",
            }
        )
    if is_eligible_for_public:
        recommended_tracks.append(
            {
                "type": "공공분양 일반공급(순차제)",
                "reason": f"1순위 요건 충족, 저축 인정총액 {recognized_krw:,}원 "
                f"(목표지역 예상 컷 {cutoffs['public_balance_min_krw']:,}원).",
            }
        )
    if is_eligible_for_private:
        if private_score >= cutoffs["private_score_min"] and is_eligible_for_private_rank1:
            recommended_tracks.append(
                {
                    "type": "민영주택 일반공급 가점제",
                    "reason": f"가점 {private_score}점으로 목표지역 예상 컷"
                    f"({cutoffs['private_score_min']}~{cutoffs['private_score_max']}점) "
                    "범위에 듭니다.",
                }
            )
        else:
            recommended_tracks.append(
                {
                    "type": "민영주택 일반공급 추첨제",
                    "reason": f"가점 {private_score}점으로 예상 컷"
                    f"({cutoffs['private_score_min']}점) 미달 — 전용 85㎡ 초과 대형 등 "
                    "추첨제 비율이 높은 단지 공략이 현실적입니다.",
                }
            )

    alternatives: list[dict[str, str]] = []
    if grade in ("S", "A") and best_pct <= 40:
        alternatives = [dict(item) for item in rules["alternative_regions"]]

    forced_matching: dict[str, Any] | None = None
    if target.is_forced_matching:
        guidance: list[str] = []
        if user.has_child_under_2:
            guidance.append(
                "Case 3 — 2세 미만 자녀 보유: 신생아 특별공급을 목표지역 1순위 타깃으로 삼으세요."
            )
        if newborn is not None or income_ratio <= first_time_cap:
            guidance.append(
                "Case 1 — 가점은 낮지만 소득·자산 요건 충족: 생애최초·신생아 등 "
                "추첨형 특별공급 전환을 권장합니다."
            )
        if newborn is None and multi is None and newlywed is None:
            guidance.append(
                "Case 2 — 특공 자격 없음: 전용 85㎡ 초과 대형 또는 추첨제 비율 높은 "
                "단지(추첨 60~100%)를 공략하세요."
            )
        if grade in ("S", "A") and best_pct <= 40:
            guidance.append(
                "Case 4 — S/A급 희망이나 점수·저축 부족: 경기 3기 신도시(사전/본청약), "
                "GTX 호재 외곽을 대안으로 검토하세요."
            )
        forced_matching = {
            "private_score_gap": max(0, cutoffs["private_score_min"] - private_score),
            "public_balance_gap_krw": max(0, cutoffs["public_balance_min_krw"] - recognized_krw),
            "guidance": guidance,
        }

    # 중복 제거(입력 순서 유지)
    deduped_notes = list(dict.fromkeys(notes))
    if optional_missing:
        deduped_notes.append(
            "다음 정보를 추가로 주면 판정이 더 정확해집니다: "
            + ", ".join(item["field"] for item in optional_missing)
        )

    return {
        "status": "ok",
        "eligibility_status": {
            "is_eligible_for_public": is_eligible_for_public,
            "is_eligible_for_private": is_eligible_for_private,
            "is_eligible_for_private_rank1": is_eligible_for_private_rank1,
            "disqualification_reasons": disqualifications,
        },
        "scores": {
            "private_general_score": private_score,
            "private_score_breakdown": private_breakdown,
            "public_balance_recognized_krw": recognized_krw,
            "special_supply_scores": special_scores,
        },
        "matching_analysis": {
            "target_region_evaluated": target_region,
            "region_grade": grade,
            "is_regulated_region": regulated,
            "expected_cutoffs": cutoffs,
            "feasibility_by_track": feasibility_by_track,
            "feasibility_level": feasibility_label(best_pct),
            "recommended_tracks": recommended_tracks,
            "alternatives": alternatives,
            "forced_matching": forced_matching,
        },
        "verification_notes": deduped_notes,
    }

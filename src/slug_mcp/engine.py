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
from .models import AccountType, ProfileDocument, missing_fields
from .rules import load_rules

_CAPITAL_SIDO_TOKENS = ("서울", "경기", "인천")

_NEWLYWED_MAX_MARRIAGE_YEARS = 7


def _mentions_admin_unit(region: str, name: str) -> bool:
    """region 안에 name이 행정구역(시/군/구) 단위로 등장하는지 판정한다.

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
    label = {80: "매우높음", 60: "높음", 40: "보통", 20: "낮음", 5: "매우낮음"}[probability_pct]
    # 프로필 점수와 지역 등급으로만 매긴 '추정' 등급이다. 특정 확률(%)이 아니라
    # 개별 공고 경쟁률과도 무관하므로 확률로 표기하지 않는다.
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


def _parse_date(value: str | None) -> date | None:
    """YYYY-MM-DD 문자열을 date로 파싱한다. 형식이 어긋나면 None(무시)."""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _age_from_birth_date(birth_date: str | None, as_of: date) -> int | None:
    """생년월일로 as_of 기준 만 나이를 계산한다. 파싱 실패 시 None."""
    born = _parse_date(birth_date)
    if born is None:
        return None
    years = as_of.year - born.year - ((as_of.month, as_of.day) < (born.month, born.day))
    return max(0, years)


def _age30_date(born: date) -> date:
    """만 30세가 되는 날. 2/29 출생은 2/28로 맞춘다."""
    try:
        return born.replace(year=born.year + 30)
    except ValueError:  # 윤일(2/29)
        return born.replace(year=born.year + 30, day=28)


def _derive_homeless_months(
    birth_date: str | None,
    homeless_since_date: str | None,
    marriage_date: str | None,
    as_of: date,
) -> int | None:
    """생년월일로 무주택 인정기간(개월)을 계산한다. 생년월일이 없으면 None(수기값 사용).

    기산 시점 = 만 30세(30세 전 혼인 시 혼인신고일). 과거 주택 처분 이력이 있어
    무주택자가 된 날(homeless_since_date)이 더 늦으면 그날부터 센다(별표 1 기산 규칙).
    """
    born = _parse_date(birth_date)
    if born is None:
        return None
    start = _age30_date(born)
    married = _parse_date(marriage_date)
    if married is not None and married < start:  # 30세 전 혼인 → 혼인신고일부터
        start = married
    became_homeless = _parse_date(homeless_since_date)
    if became_homeless is not None and became_homeless > start:  # 처분 후 무주택이 더 늦으면
        start = became_homeless
    if start >= as_of:
        return 0
    return (as_of.year - start.year) * 12 + (as_of.month - start.month)


def homeless_household_member(
    household_owns_home: bool,
    owns_home_self: bool | None,
    home_owner_is_ascendant_60plus: bool | None,
) -> tuple[bool, str]:
    """무주택세대구성원 여부와 판정 근거를 돌려준다 (is_homeless, basis).

    청약 무주택 판정은 개인이 아니라 세대(주민등록등본) 단위다. 세대가 주택을 보유해도
    그 소유자가 본인이 아니라 만 60세 이상 직계존속이면 주택공급규칙 제53조로 무주택
    간주한다(노부모부양 특공·공공임대는 예외지만 둘 다 미구현이라 충돌 없음).

    - 세대 무주택 → 무주택 (basis="no_owned_house")
    - 세대 보유 & 본인/배우자 소유 → 유주택 (예외 없음, basis="owns_home_self")
    - 세대 보유 & 만 60세 이상 직계존속 소유 → 무주택 간주
      (basis="elderly_ascendant_exception")
    - 세대 보유 & 그 외 → 유주택 세대 (basis="household_owns_home")
    """
    if not household_owns_home:
        return True, "no_owned_house"
    if owns_home_self is True:
        return False, "owns_home_self"
    if home_owner_is_ascendant_60plus is True:
        return True, "elderly_ascendant_exception"
    return False, "household_owns_home"


def _homeless_recognized_cap_months(
    age: int, is_married: bool, marriage_date: str | None, as_of: date
) -> int:
    """무주택기간 인정 상한(개월). 만 30세부터, 30세 전 혼인 시 혼인신고일부터 기산한다.

    별표 1 기산 규칙의 근사 — 생년월일이 없어 만 나이로 만 30세 시점을 어림한다.
    """
    cap = max(0, age - 30) * 12
    if is_married and marriage_date:
        cap = max(cap, int(_years_since(marriage_date, as_of) * 12))
    return cap


def _income_within(caps: dict[str, Any], key: str, income_ratio: float, is_dual: bool) -> bool:
    """소득비율이 해당 공급유형의 진입 상한(외벌이 single/맞벌이 dual) 이하인지."""
    cap = caps[key]
    return income_ratio <= (cap["dual"] if is_dual else cap["single"])


# 월 납입 인정 상한 25만원 시행일. 이 날 전 도래분은 종전 10만원 한도(부칙 제4조②).
_MONTHLY_CAP_EFFECTIVE = date(2024, 11, 1)


def _months_before_cap_change(duration_months: int, as_of: date) -> int:
    """가입기간 중 25만원 상한 시행일(2024-11-01) 이전에 도래한 개월 수(근사)."""
    if as_of <= _MONTHLY_CAP_EFFECTIVE:
        return duration_months
    months_since = (as_of.year - _MONTHLY_CAP_EFFECTIVE.year) * 12 + (
        as_of.month - _MONTHLY_CAP_EFFECTIVE.month
    )
    return max(0, duration_months - months_since)


def analyze(
    doc: dict[str, Any], as_of: date | None = None, region_override: str | None = None
) -> dict[str, Any]:
    """프로필 문서를 판정해 스펙 §7 출력 스키마를 돌려준다.

    필수 항목이 비어 있으면 status="needs_more_info"와 함께 클라이언트 AI가
    사용자에게 물어볼 질문 목록을 돌려준다.

    region_override를 주면 목표지역 대신 그 지역으로 규제 여부·컷오프 등급을 판정한다
    (추천 공고별 위치로 규제지역 자격을 다시 따질 때 사용).
    """
    as_of = as_of or date.today()
    core_missing, full_missing, optional_missing = missing_fields(doc)
    if core_missing:
        return {
            "status": "needs_more_info",
            "missing_required_fields": core_missing,
            "missing_recommended_fields": full_missing,
            "missing_optional_fields": optional_missing,
            "guidance": "잠정 판정에 필요한 core 항목을 채운 뒤 다시 분석하세요. "
            "update_my_profile로 부분 업데이트할 수 있습니다.",
        }
    # core는 채워졌다. full이 비면 '잠정 판정'으로 수행하고, 비는 항목을 채우라고 안내한다.
    is_provisional = bool(full_missing)

    profile_doc = ProfileDocument.model_validate(doc)
    user = profile_doc.user_profile
    account = profile_doc.subscription_account
    target = profile_doc.target_housing
    rules = load_rules()

    notes: list[str] = []
    action_items: list[str] = []  # 사용자가 채우면 판정이 바뀌거나 정확해지는 항목(D)
    disqualifications: list[dict[str, Any]] = []

    # core 검증을 통과했으므로 core 필드는 None이 아니다 (mypy·가독성용 지역변수).
    marriage_date = user.marriage.marriage_date
    # 생년월일이 있으면 만 나이·무주택기간을 정확히 산정한다(수기 age·개월수보다 우선).
    derived_age = _age_from_birth_date(user.birth_date, as_of)
    age = derived_age if derived_age is not None else (user.age or 0)
    derived_homeless = _derive_homeless_months(
        user.birth_date, user.homeless_since_date, marriage_date, as_of
    )
    homeless_from_birth_date = derived_homeless is not None
    homeless_months = (
        derived_homeless if derived_homeless is not None else (user.homeless_duration_months or 0)
    )
    dependents = user.dependents_count or 0
    monthly_income = user.income_and_assets.monthly_income_krw or 0
    duration_months = account.duration_months or 0
    balance_krw = account.total_balance_krw or 0
    residence_area = user.residence_area or ""
    target_region = region_override or target.target_region or residence_area

    # full 항목 미입력 여부 — 잠정 판정에서 해당 트랙 점수를 '미확정'으로 다루기 위한 플래그.
    balance_unknown = account.total_balance_krw is None
    income_unknown = user.income_and_assets.monthly_income_krw is None
    dependents_unknown = user.dependents_count is None
    marriage_unknown = user.marriage.is_married is None
    homeless_unknown = user.homeless_duration_months is None and not homeless_from_birth_date

    is_married = bool(user.marriage.is_married)
    children = user.children_count or 0
    infants = user.infants_count or 0
    household_size = dependents + 1  # 부양가족(본인 제외) + 본인
    income_ratio = scoring.income_ratio_pct(monthly_income, household_size, rules)
    residence_years = user.residence_years_in_region or 0
    account_years = duration_months // 12
    regulated = is_regulated_region(target_region)
    desired_size = target.desired_size_sqm
    owned_house_count = user.owned_house_count
    account_type = account.account_type
    # 유주택 판정(세대 단위): 주택 보유 수를 우선 사용하고, 미입력 시에만 무주택기간 0 프록시로
    # 대체한다. 세대가 보유해도 만 60세 이상 직계존속 소유면 제53조로 무주택 간주한다.
    household_owns_home = (
        owned_house_count > 0 if owned_house_count is not None else homeless_months == 0
    )
    is_homeless_hhm, ownership_basis = homeless_household_member(
        household_owns_home, user.owns_home_self, user.home_owner_is_ascendant_60plus
    )
    is_homeowner = not is_homeless_hhm
    # 유주택 세대면 무주택기간 가점이 붙지 않게 0으로 누른다(입력값 오적용 방지).
    effective_homeless_months = homeless_months if is_homeless_hhm else 0
    homeless_years = effective_homeless_months // 12

    # 생년월일로 무주택기간을 계산했으면 그 사실과 정밀화 여지를 알린다.
    if homeless_from_birth_date and is_homeless_hhm:
        note = "생년월일 기준으로 무주택 인정기간을 계산했습니다"
        note += (
            "(과거 주택 처분 이력이 있으면 무주택 시작일을 입력하면 더 정확해집니다)."
            if not user.homeless_since_date
            else "."
        )
        notes.append(note)
    # 잠정 판정 안내(D) — full 미입력 항목이 어떤 판정을 정확하게 하는지 사용자에게 알린다.
    if balance_unknown:
        action_items.append(
            "청약통장 납입총액(예치금)을 입력하면 민영 1순위 예치금과 공공 인정총액을 판정합니다."
        )
    if income_unknown:
        action_items.append("가구 월평균 소득을 입력하면 특별공급 자격과 소득요건을 판정합니다.")
    if dependents_unknown:
        action_items.append(
            "부양가족 수를 입력하면 민영 가점의 부양가족 항목(최대 35점)이 정확해집니다."
        )
    if marriage_unknown:
        action_items.append(
            "혼인 여부를 입력하면 신혼부부 특별공급과 무주택기간 기산이 정확해집니다."
        )
    if homeless_unknown:
        action_items.append("무주택 기간(또는 생년월일)을 입력하면 무주택기간 가점이 정확해집니다.")

    # ---- 1단계 Hard Filter (§3) ------------------------------------------
    # 민영 특공은 미구현(스펙 §5.C 🔴 미검증)이라 별도 차단 플래그 없이
    # public_blocked가 공공 트랙 전체(일반+특공)를 함께 가른다.
    public_blocked = False
    private_rank1_blocked = False
    private_account_blocked = False  # 통장 유형(C-5)으로 민영 트랙이 막히는 경우

    # Filter-01 · 무주택 요건. 유주택 세대는 공공 전체·민영 특공 불가.
    # 민영 일반공급(가점제)은 비규제지역에서만 유주택자 신청 가능(무주택 0점).
    # 규제지역에서는 유주택 세대가 가점제 대상에서도 제외된다(규칙 제28조⑥ — B-12).
    if owned_house_count is None:
        notes.append(
            "주택 보유 수 미입력 — 무주택기간 0개월을 유주택으로 간주했습니다. 막 처분해 "
            "무주택 0개월인 경우 실제 자격과 다를 수 있으니 주택 보유 수를 입력하세요."
        )
    homeowner_blocks_private_general = is_homeowner and regulated
    if is_homeowner:
        public_blocked = True
        # 소유자가 본인이 아니면(세대원 소유), 60세 예외·세대분리로 무주택 인정 여지를 안내한다.
        exception_hint = (
            " 다만 그 주택 소유자가 만 60세 이상 직계존속이거나 세대분리를 하면 무주택자로 "
            "인정될 수 있습니다(home_owner_is_ascendant_60plus 확인)."
            if ownership_basis == "household_owns_home"
            else ""
        )
        disqualifications.append(
            {
                "filter": "Filter-01(무주택)",
                "reason": "유주택 세대는 공공분양 전체와 민영 특별공급에 신청할 수 없습니다. "
                + (
                    "목표지역이 규제지역이라 민영 일반공급 가점제 대상에서도 제외됩니다"
                    "(규칙 제28조⑥)."
                    if regulated
                    else "민영 일반공급(가점제)은 신청 가능하나 무주택기간 가점이 0점입니다."
                )
                + exception_hint,
                "blocked_tracks": ["public_all", "private_special"]
                + (["private_general"] if regulated else []),
            }
        )
    elif ownership_basis == "elderly_ascendant_exception":
        notes.append(
            "만 60세 이상 직계존속이 소유한 주택은 주택공급규칙 제53조에 따라 무주택으로 "
            "간주했습니다(공동명의면 부모 전원이 60세 이상이어야 하며, 노부모부양 특별공급·"
            "공공임대는 예외입니다). 실제 신청 전 청약홈 무주택세대구성원 자가진단으로 "
            "재확인하세요(🟡)."
        )

    # Filter-02 · 공공분양 자산 컷. 특별공급 전체(면적 무관) + 60㎡ 이하 일반공급에 적용(B-9).
    asset_limits = rules["asset_limits"]
    real_estate = user.income_and_assets.total_real_estate_krw
    car_value = user.income_and_assets.car_value_krw
    real_estate_cap = asset_limits["real_estate_10k_won"] * 10_000
    car_cap = asset_limits["vehicle_10k_won"] * 10_000
    over_real_estate = real_estate is not None and real_estate > real_estate_cap
    over_car = car_value is not None and car_value > car_cap
    asset_exceeds = over_real_estate or over_car
    if asset_exceeds:
        reasons = []
        if over_real_estate:
            reasons.append(f"부동산 자산({real_estate:,}원 > {real_estate_cap:,}원)")
        if over_car:
            reasons.append(f"자동차 가액({car_value:,}원 > {car_cap:,}원)")
        # 60㎡ 이하 일반공급은 자산 초과 시 진입 불가. 특별공급은 아래 특공 분기에서 배제.
        if desired_size is not None and desired_size <= 60:
            public_blocked = True
        disqualifications.append(
            {
                "filter": "Filter-02(자산)",
                "reason": "공공분양 자산 기준 초과 — "
                + ", ".join(reasons)
                + ". 공공 특별공급 전체와 전용 60㎡ 이하 일반공급에 신청할 수 없습니다.",
                "blocked_tracks": ["public_special", "public_under_60sqm"],
            }
        )
    if not is_homeowner:
        notes.append(
            "공공분양 자산 상한(부동산 2.155억/자동차 4,542만원)은 특별공급 전체와 60㎡ 이하 "
            "일반공급에 적용됩니다. 2023-03-28 이후 출생 자녀가 있는 출산가구는 110~120% 완화가 "
            "있으나 자녀 출생일 미수집으로 미반영이며(실제보다 엄격할 수 있음), 나눔형·선택형은 "
            "총자산 약 3.62억 별도 기준입니다."
        )
        if real_estate is None or car_value is None:
            notes.append(
                "부동산·자동차 자산이 미입력이라 공공분양 자산 기준을 완전히 확인하지 못했습니다."
            )

    # Filter-03 · 규제지역 1순위 제한 (세대주·2주택·재당첨) — 동적 규제지역 목록
    if regulated:
        notes.append(
            "규제지역 판정은 2026-07-01 추가지정 반영(서울 전역+경기 15곳) 목록 기준입니다."
        )
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
        if owned_house_count is not None and owned_house_count >= 2:
            private_rank1_blocked = True
            disqualifications.append(
                {
                    "filter": "Filter-03(규제지역 2주택)",
                    "reason": f"규제지역은 2주택 이상 소유 세대({owned_house_count}주택)의 "
                    "1순위 신청이 제한됩니다(규칙 제28조①1호다목).",
                    "blocked_tracks": ["private_rank1", "public_general_rank1"],
                }
            )
        if user.marriage.pre_marriage_win_history:
            private_rank1_blocked = True
            disqualifications.append(
                {
                    "filter": "Filter-03(규제지역 재당첨)",
                    "reason": "규제지역 1순위는 과거 당첨된 세대(배우자의 혼인 전 당첨 포함)를 "
                    "제한합니다.",
                    "blocked_tracks": ["private_rank1"],
                }
            )
        notes.append(
            "재당첨 제한(규칙 제54조: 투기과열 10년·청약과열 7년 등)은 당첨 일자·유형 "
            "미수집으로 미반영입니다 — 청약 전 청약홈에서 재당첨 제한 여부를 확인하세요."
        )

    # Filter-04 · 청약통장 유형별 신청 제약 (C-5)
    if account_type == AccountType.PUBLIC_SAVINGS:  # 청약저축 — 국민(공공)주택만
        private_account_blocked = True
        notes.append("청약저축은 국민(공공)주택만 신청 가능해 민영주택 트랙을 제외했습니다.")
    elif account_type == AccountType.PRIVATE_DEPOSIT:  # 청약예금 — 민영주택만
        public_blocked = True
        notes.append("청약예금은 민영주택만 신청 가능해 공공(국민)주택 트랙을 제외했습니다.")
    elif account_type == AccountType.PRIVATE_INSTALLMENT:  # 청약부금 — 85㎡↓ 민영만
        public_blocked = True
        notes.append("청약부금은 공공(국민)주택 트랙을 제외하고 전용 85㎡ 이하 민영만 가능합니다.")
        if desired_size is not None and desired_size > 85:
            private_account_blocked = True
            notes.append("청약부금은 전용 85㎡를 초과하는 민영주택은 신청할 수 없습니다.")

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
        # 2024-11-01 전 도래 회차는 종전 10만원, 이후는 25만원 한도로 인정(부칙 제4조②).
        # 가입기간으로 이전/이후 회차를 근사하고 이른 회차를 이전분으로 본다.
        old_count = min(payments, _months_before_cap_change(duration_months, as_of))
        recognized_cap = old_count * 100_000 + (payments - old_count) * monthly_cap
        recognized_krw = min(balance_krw, recognized_cap)
        notes.append(
            "공공 인정총액은 2024-11-01 전 회차는 월 10만원, 이후는 25만원 한도로 계산했습니다 "
            "(가입기간 기준 근사 — 정확한 회차별 납입일은 통장·공고 원문으로 확인)."
        )
    else:
        recognized_krw = balance_krw
        notes.append("납입횟수 미입력 — 공공 인정총액을 납입총액 그대로 사용했습니다.")
    if desired_size is not None and desired_size <= 40:
        notes.append("전용 40㎡ 이하는 저축총액이 아닌 납입횟수 순으로 선정됩니다(§4.A).")

    is_eligible_for_public = not public_blocked and public_rank1_ok

    # ---- 특별공급 매칭·배점 (§2.B) — 전부 공공분양 트랙 ---------------------
    # 특공은 무주택 세대구성원 + 청약통장 보유가 공통 전제(A-9)이고, 소득 상한을 넘으면
    # 해당 특공 자체가 부적격(A-2·A-3)이다. 신혼·신생아 배점은 가장 흔한 일반형 기준.
    special_scores: dict[str, Any] = {"newborn": None, "newlywed": None, "multi_child": None}
    is_dual = user.income_and_assets.is_dual_income
    caps = rules["income_ratio_by_supply_type"]

    if not public_blocked and duration_months > 0 and not asset_exceeds and not income_unknown:
        # ① 신생아 특별공급 (혼인 무관, 2세 미만 자녀)
        if user.has_child_under_2:
            track = scoring.newborn_track(income_ratio, is_dual, rules["newborn_supply"])
            if track is not None:
                special_scores["newborn"] = {
                    "track": track,
                    "track_share_pct": rules["newborn_supply"][track]["share"],
                    "income_ratio_pct": round(income_ratio, 1),
                    "priority_general_score": scoring.newborn_score(
                        income_ratio_pct=income_ratio,
                        is_dual_income=is_dual,
                        residence_years=residence_years,
                        payment_count=payments or 0,
                        children_count=children,
                        table=rules["newborn_score_table"],
                    ),
                }
                notes.append(
                    "신생아 특공은 물량 배정만으로 당첨이 정해지지 않고, 우선·일반공급은 경쟁 시 "
                    "배점(가구소득·자녀수·거주기간·납입횟수 10점)·추첨으로 선정됩니다."
                )
            else:
                notes.append(
                    f"신생아 특공: 소득비율 {income_ratio:.0f}%가 소득 상한(외벌이 140%/"
                    "맞벌이 200%)을 초과해 대상이 아닙니다."
                )

        # ② 신혼부부 특별공급 — 혼인 7년 이내 또는 6세 이하 자녀·한부모 (B-6), 소득 상한 게이트
        marriage_years = (
            _years_since(user.marriage.marriage_date, as_of)
            if user.marriage.marriage_date
            else None
        )
        has_young_child = bool(user.has_child_under_2) or infants > 0
        newlywed_eligible = False
        if is_married:
            if marriage_years is None:
                newlywed_eligible = True
                notes.append(
                    "혼인신고일 미입력 — 신혼부부 특공의 '혼인 7년 이내' 요건을 "
                    "확인하지 못했습니다."
                )
            elif marriage_years <= _NEWLYWED_MAX_MARRIAGE_YEARS or has_young_child:
                newlywed_eligible = True
        elif user.is_single_parent and has_young_child:
            newlywed_eligible = True  # 한부모가족(6세 이하 자녀)
        if newlywed_eligible and not _income_within(caps, "newlywed", income_ratio, is_dual):
            newlywed_eligible = False
            notes.append(
                f"신혼부부 특공: 소득비율 {income_ratio:.0f}%가 소득 상한(외벌이 "
                f"{caps['newlywed']['single']}%/맞벌이 {caps['newlywed']['dual']}%)을 초과해 "
                "대상이 아닙니다."
            )
        if newlywed_eligible:
            special_scores["newlywed"] = scoring.newlywed_score(
                income_ratio_pct=income_ratio,
                is_dual_income=is_dual,
                residence_years=residence_years,
                payment_count=payments or 0,
                children_count=children,
                marriage_years=marriage_years,
                is_single_parent=user.is_single_parent,
                has_child_under_2=bool(user.has_child_under_2),
                infants_count=infants,
                table=rules["newlywed_score_table"],
            )
            notes.append(
                "신혼부부 특공 배점은 일반형(별표 6) 순위제+13점 기준입니다 — 나눔형·토지임대부는 "
                "9/12점 별도 체계이니 공고 유형을 확인하세요(예비신혼부부는 미지원)."
            )

        # ③ 다자녀 특별공급 — 미성년 자녀 2명 이상 + 공공 소득 상한 게이트 (A-3)
        if children >= 2:
            if _income_within(caps, "multi_child", income_ratio, is_dual):
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
            else:
                notes.append(
                    f"다자녀 특공: 소득비율 {income_ratio:.0f}%가 공공 소득 상한(외벌이 "
                    f"{caps['multi_child']['single']}%/맞벌이 {caps['multi_child']['dual']}%)을 "
                    "초과해 대상이 아닙니다(민영 다자녀는 소득요건 없음)."
                )
    elif not public_blocked and asset_exceeds:
        notes.append("공공 자산 기준 초과로 특별공급 대상에서도 제외했습니다(면적 무관).")
    elif not public_blocked and duration_months == 0:
        notes.append(
            "청약통장이 없어 공공 특별공급 대상에서 제외했습니다(특공도 통장 보유가 전제)."
        )
    elif not public_blocked and income_unknown:
        notes.append("소득 미입력 — 특별공급 자격·배점은 가구 소득을 입력해야 판정할 수 있습니다.")

    # ---- 2단계 [BRANCH B] 민영주택 (§5) ------------------------------------
    if homeless_from_birth_date:
        # 생년월일로 이미 만 30세·혼인일 기산을 정확히 반영했으므로 추가 상한·근사 경고가 불필요.
        homeless_cap_months = effective_homeless_months
    else:
        homeless_cap_months = _homeless_recognized_cap_months(age, is_married, marriage_date, as_of)
        if homeless_cap_months < effective_homeless_months:
            notes.append(
                "무주택기간 가점은 만 30세(또는 그 전 혼인신고일)부터만 인정되어 입력값보다 "
                "짧게 반영했습니다(생년월일이 없어 만 나이 기준 근사)."
            )
    private_breakdown = scoring.private_general_score(
        age=age,
        is_married=is_married,
        homeless_duration_months=effective_homeless_months,
        dependents_count=dependents,
        duration_months=duration_months,
        spouse_duration_months=account.spouse_duration_months,
        homeless_recognized_cap_months=homeless_cap_months,
    )

    deposit_table = rules["subscription_account"]["private"]["deposit_table_10k_won"]
    bracket = _deposit_area_bracket(desired_size if desired_size is not None else 85)
    required_deposit_krw = deposit_table[bracket][_region_tier(residence_area)] * 10_000
    # 예치금 미입력(잠정 판정)이면 박탈로 단정하지 않고, 채우라고 안내만 한다.
    deposit_ok = balance_krw >= required_deposit_krw
    if not deposit_ok and not balance_unknown:
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
    is_eligible_for_private = has_account and not private_account_blocked
    if not has_account:
        disqualifications.append(
            {
                "filter": "Rank1(민영 통장)",
                "reason": "청약통장이 없어 민영주택 순위 신청이 불가합니다.",
                "blocked_tracks": ["private_all"],
            }
        )

    # A-1 · 민영 1순위 가입기간 요건 (공급지역 기준, 규칙 제28조①1호). 민영은 예치금
    # 기준이라 납입횟수 요건은 없다. 예치금은 충족해도 가입기간 미달이면 2순위.
    private_rank1_months_cfg = rules["subscription_account"]["private"]["rank1_min_months"]
    if regulated:
        private_rank1_min_months = private_rank1_months_cfg["overheated_region"]
    elif any(token in target_region for token in _CAPITAL_SIDO_TOKENS):
        private_rank1_min_months = private_rank1_months_cfg["capital_region"]
    else:
        private_rank1_min_months = private_rank1_months_cfg["non_capital_region"]
    private_duration_ok = duration_months >= private_rank1_min_months
    if has_account and deposit_ok and not private_duration_ok:
        disqualifications.append(
            {
                "filter": "Rank1(민영 가입기간)",
                "reason": f"민영 1순위는 청약통장 가입 {private_rank1_min_months}개월 이상이 "
                f"필요합니다(현재 {duration_months}개월). 예치금은 충족했으나 2순위입니다.",
                "blocked_tracks": ["private_rank1"],
            }
        )
    is_eligible_for_private_rank1 = (
        has_account
        and deposit_ok
        and private_duration_ok
        and not private_rank1_blocked
        and not private_account_blocked
    )

    # ---- 4단계 컷오프 대조 + 강제 매칭 (§6) --------------------------------
    grade = region_grade(target_region)
    cutoffs = rules["expected_cutoffs"][grade]
    notes.append(
        "expected_cutoffs는 지역 등급 기반 참고 기준선(2026 계획 추정치)이지 실제 당첨 컷이 "
        "아닙니다(cutoff_basis=planning_estimate). 특정 단지의 실제 경쟁률은 "
        "recommend_housing의 유사 과거 실적으로 확인하세요."
    )
    private_score: int = private_breakdown["total"]
    private_pct = private_feasibility_pct(
        private_score, cutoffs["private_score_min"], cutoffs["private_score_max"]
    )
    public_pct = public_feasibility_pct(recognized_krw, cutoffs["public_balance_min_krw"])

    # 예치금 미입력이면 공공 인정총액(recognized_krw)이 0이라 실현가능성을 '미확정'으로 둔다.
    public_feasible = is_eligible_for_public and not balance_unknown
    feasibility_by_track = {
        "private_general": feasibility_label(private_pct if is_eligible_for_private else 5),
        "public_general": (
            "미확정(예치금 미입력)"
            if (is_eligible_for_public and balance_unknown)
            else feasibility_label(public_pct if is_eligible_for_public else 5)
        ),
    }
    best_pct = max(
        private_pct if is_eligible_for_private else 5,
        public_pct if public_feasible else 5,
    )

    recommended_tracks: list[dict[str, str]] = []
    newborn = special_scores["newborn"]
    if newborn is not None:
        track_names = {
            "priority": "우선공급(70%)",
            "general": "일반공급(20%)",
            "lottery": "추첨공급(10%)",
        }
        pg = newborn.get("priority_general_score")
        score_txt = (
            f" 경쟁 배점 {pg['total']}/10점,"
            if pg is not None and newborn["track"] in ("priority", "general")
            else ""
        )
        recommended_tracks.append(
            {
                "type": f"신생아 특별공급(공공) {track_names[newborn['track']]}",
                "reason": f"2세 미만 자녀가 있고 소득비율 {newborn['income_ratio_pct']}%로 "
                f"{track_names[newborn['track']]} 트랙에 해당합니다.{score_txt} 물량 배정만으로 "
                "당첨이 확정되지 않고, 경쟁 시 해당지역·배점·추첨으로 선정됩니다.",
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
                "type": "신혼부부 특별공급(공공, 일반형)",
                "reason": f"{newlywed['rank']}순위, 경쟁 배점 {newlywed['total']}/13점"
                "(별표 6 일반형). 나눔형·토지임대부는 9/12점 별도 체계입니다.",
            }
        )
    # 생애최초 특공: 무주택 세대 + 통장 보유 + 소득 상한(외벌이/맞벌이). 세부 요건은 공고 확인.
    first_time_ok = (
        not public_blocked
        and duration_months > 0
        and (is_married or children > 0)
        and not is_homeowner
        and not income_unknown
        and _income_within(caps, "first_time", income_ratio, is_dual)
    )
    if first_time_ok:
        ftc = caps["first_time"]
        recommended_tracks.append(
            {
                "type": "생애최초 특별공급",
                "reason": f"무주택·소득요건(외벌이 {ftc['single']}%/맞벌이 {ftc['dual']}%) 충족 시 "
                "가점 무관 추첨 트랙입니다. 생애 최초 주택 구입·5년 이상 소득세 납부 등 세부 "
                "요건은 공고문으로 확인하세요.",
            }
        )
    if is_eligible_for_public and balance_unknown:
        recommended_tracks.append(
            {
                "type": "공공분양 일반공급(순차제)",
                "reason": "1순위 가입기간 요건은 충족합니다. 납입총액(예치금)을 입력하면 저축 "
                f"인정총액을 목표지역 참고 기준선({cutoffs['public_balance_min_krw']:,}원 — 관측값 "
                "아님)과 대조해 드립니다.",
            }
        )
    elif is_eligible_for_public:
        recommended_tracks.append(
            {
                "type": "공공분양 일반공급(순차제)",
                "reason": f"1순위 요건 충족, 저축 인정총액 {recognized_krw:,}원 "
                f"(목표지역 참고 기준선 {cutoffs['public_balance_min_krw']:,}원 — 관측값 아님).",
            }
        )
    # 민영 일반공급: 규제지역 유주택 세대는 가점제 대상에서 제외(B-12).
    if is_eligible_for_private and not homeowner_blocks_private_general:
        if private_score >= cutoffs["private_score_min"] and is_eligible_for_private_rank1:
            recommended_tracks.append(
                {
                    "type": "민영주택 일반공급 가점제",
                    "reason": f"가점 {private_score}점으로 목표지역 참고 기준선"
                    f"({cutoffs['private_score_min']}~{cutoffs['private_score_max']}점, 관측값 "
                    "아님) 범위에 듭니다. 실제 경쟁률은 recommend_housing으로 확인하세요.",
                }
            )
        elif regulated:
            # A-7: 규제지역은 추첨 물량이 적다(85㎡ 초과도 투기과열 20%/청약과열 50%).
            recommended_tracks.append(
                {
                    "type": "민영주택 일반공급 추첨제(규제지역·물량 적음)",
                    "reason": f"가점 {private_score}점으로 참고 기준선"
                    f"({cutoffs['private_score_min']}점, 관측값 아님) 미달. 규제지역은 추첨 물량이 "
                    "적어 "
                    "(85㎡ 초과도 투기과열 20%/청약과열 50%) 당첨 가능성이 낮으니 "
                    "특별공급·비규제 대안을 함께 검토하세요.",
                }
            )
        else:
            recommended_tracks.append(
                {
                    "type": "민영주택 일반공급 추첨제",
                    "reason": f"가점 {private_score}점으로 참고 기준선"
                    f"({cutoffs['private_score_min']}점, 관측값 아님) 미달 — 비규제지역은 전용 "
                    "85㎡ 초과 대형 등 추첨 물량(추첨 100%)이 많아 공략이 현실적입니다.",
                }
            )
    elif is_eligible_for_private and homeowner_blocks_private_general:
        notes.append(
            "규제지역 유주택 세대는 민영 가점제 대상에서도 제외되어 민영 일반공급 추천을 "
            "생략했습니다(규칙 제28조⑥)."
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
        if newborn is not None or (
            not income_unknown and _income_within(caps, "first_time", income_ratio, is_dual)
        ):
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
            # 예치금 미입력이면 공공 gap을 단정하지 않는다(None).
            "public_balance_gap_krw": (
                None
                if balance_unknown
                else max(0, cutoffs["public_balance_min_krw"] - recognized_krw)
            ),
            "guidance": guidance,
        }

    # optional 미입력은 '더 채우면 정확해지는' 행동 안내라 action_items로 모은다(D).
    if optional_missing:
        action_items.append(
            "다음 정보를 추가로 주면 판정이 더 정확해집니다: "
            + ", ".join(item["field"] for item in optional_missing)
        )

    # 중복 제거(입력 순서 유지)
    deduped_notes = list(dict.fromkeys(notes))
    deduped_actions = list(dict.fromkeys(action_items))

    # 한 줄 결론(D). 가장 유망한 트랙 또는 막힌 사유를 요약하고, 잠정 판정이면 표시한다.
    if recommended_tracks:
        headline_core = f"가장 유망한 트랙은 '{recommended_tracks[0]['type']}'입니다"
    elif disqualifications:
        blocker = disqualifications[0]["filter"]
        headline_core = f"현재 조건으로 신청 가능한 트랙이 없습니다(주요 사유: {blocker})"
    else:
        headline_core = "신청 가능한 트랙을 찾지 못했습니다"
    headline = ("[잠정 판정] " if is_provisional else "") + headline_core + "."
    if is_provisional or deduped_actions:
        headline += " (선택) 추가 정보를 넣으면 판정이 더 정밀해집니다."

    return {
        "status": "ok",
        "confidence": "provisional" if is_provisional else "complete",
        "headline": headline,
        "eligibility_status": {
            "is_eligible_for_public": is_eligible_for_public,
            "is_eligible_for_private": is_eligible_for_private,
            "is_eligible_for_private_rank1": is_eligible_for_private_rank1,
            "is_homeless_household_member": is_homeless_hhm,
            "homeless_status_basis": ownership_basis,
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
            "cutoff_basis": "planning_estimate",  # 지역 등급 기반 참고 기준선(관측값 아님)
            "feasibility_by_track": feasibility_by_track,
            "feasibility_level": feasibility_label(best_pct),
            "recommended_tracks": recommended_tracks,
            "alternatives": alternatives,
            "forced_matching": forced_matching,
        },
        "action_items": deduped_actions,
        "verification_notes": deduped_notes,
    }

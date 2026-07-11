"""MCP 도구가 주고받는 입출력 스키마.

이 파일의 필드명과 설명(description)은 MCP 클라이언트(사용자 쪽 AI)가
자연어 대화에서 값을 뽑아내 채우는 근거가 되므로, 이름과 설명을 명확하게 유지한다.

프로필 스키마는 docs/subscription-policy-spec.md §1 입력 스키마를 따르며,
대화 몇 번에 걸쳐 조각조각 채워지도록 모든 필드가 선택 입력이다.
필수 항목이 비어 있으면 도구가 "다음에 물어볼 질문"을 돌려준다.
금액 필드는 전부 원(KRW) 단위이고 필드명에 _krw 를 붙인다.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class HouseCategory(StrEnum):
    """분양 공고의 대분류."""

    APT = "apt"  # 아파트
    OFFICETEL = "officetel"  # 오피스텔/도시형생활주택/생활숙박시설/민간임대
    REMAINDER = "remainder"  # 무순위/잔여세대


class LhNoticeType(StrEnum):
    """LH(한국토지주택공사) 분양·임대 공고유형.

    HouseCategory와 같은 관례로, 값은 의미어이고 실제 API 코드(UPP_AIS_TP_CD)로의
    변환은 도구 계층(tools/lh_lease.py)에서 한다.
    """

    LAND = "land"  # 토지
    SALE_HOUSE = "sale_house"  # 분양주택
    LEASE_HOUSE = "lease_house"  # 임대주택
    HOUSING_WELFARE = "housing_welfare"  # 주거복지
    STORE = "store"  # 상가
    NEWLYWED_HOPE = "newlywed_hope"  # 신혼희망타운


# LH 공고 지역코드(CNP_CD)는 행정표준 시도 코드를 따른다. 도구는 사용자가
# 시도명(예: "경기")으로 넘기면 이 표에서 코드를 찾아 API 파라미터로 변환한다.
LH_REGION_CODES: dict[str, str] = {
    "서울": "11",
    "부산": "26",
    "대구": "27",
    "인천": "28",
    "광주": "29",
    "대전": "30",
    "울산": "31",
    "세종": "36",
    "경기": "41",
    "강원": "42",
    "충북": "43",
    "충남": "44",
    "전북": "45",
    "전남": "46",
    "경북": "47",
    "경남": "48",
    "제주": "50",
}


class TargetHousing(BaseModel):
    """희망하는 청약 대상."""

    target_region: str | None = Field(
        default=None,
        description="희망 공급지역 (예: '서울 마포구', '경기 하남', '부산'). "
        "시·군·구까지 주면 정확해짐",
    )
    desired_size_sqm: float | None = Field(
        default=None,
        ge=0,
        description="희망 전용면적(㎡). 예: 59, 84. 민영 예치금·공공 자산기준 판정에 사용",
    )
    is_forced_matching: bool = Field(
        default=False, description="점수가 목표지역 당첨선에 미달해도 우회 전략 분석을 원하면 true"
    )


class Marriage(BaseModel):
    """혼인 상태. 신혼부부 특공과 무주택기간 가점 산정에 쓰인다."""

    is_married: bool | None = Field(default=None, description="혼인신고 기준 기혼 여부")
    marriage_date: str | None = Field(
        default=None, description="혼인신고일 (YYYY-MM-DD). 신혼부부 특공(7년 이내) 판정에 필요"
    )
    pre_marriage_win_history: bool = Field(
        default=False, description="배우자의 혼인 전 청약 당첨 이력 여부"
    )


class IncomeAssets(BaseModel):
    """가구 소득·자산. 특별공급 소득트랙과 공공분양 자산 컷 판정에 쓰인다."""

    monthly_income_krw: int | None = Field(
        default=None, ge=0, description="가구 세전 월평균 소득 합계 (원)"
    )
    is_dual_income: bool = Field(default=False, description="맞벌이 여부")
    total_real_estate_krw: int | None = Field(
        default=None, ge=0, description="세대 보유 부동산 자산 총합 (원)"
    )
    car_value_krw: int | None = Field(default=None, ge=0, description="세대 보유 자동차 가액 (원)")


class AccountType(StrEnum):
    """청약통장 유형. 유형별로 신청 가능한 주택이 다르다.

    2015-09-01부터 주택청약종합저축만 신규 가입 가능하며, 종전 통장은 유지·전환된다.
    """

    COMPREHENSIVE = "comprehensive"  # 주택청약종합저축 — 국민·민영 전부
    PUBLIC_SAVINGS = "public_savings"  # 청약저축 — 국민(공공)주택만
    PRIVATE_DEPOSIT = "private_deposit"  # 청약예금 — 민영주택만
    PRIVATE_INSTALLMENT = "private_installment"  # 청약부금 — 전용 85㎡ 이하 민영만


class SubscriptionAccount(BaseModel):
    """청약통장 현황. 공공은 납입횟수·인정총액, 민영은 예치금(총액)·가입기간이 핵심."""

    account_type: AccountType = Field(
        default=AccountType.COMPREHENSIVE,
        description="청약통장 유형. 종합저축은 전부 신청 가능, 청약저축은 국민(공공)만, "
        "청약예금은 민영만, 청약부금은 전용 85㎡ 이하 민영만. 미입력 시 종합저축으로 가정",
    )
    duration_months: int | None = Field(
        default=None, ge=0, description="본인 청약통장 가입기간 (개월). 통장이 없으면 0"
    )
    payment_count: int | None = Field(default=None, ge=0, description="본인 납입 횟수 (공공분양용)")
    total_balance_krw: int | None = Field(
        default=None, ge=0, description="납입 인정 총액(공공용)이자 예치금(민영용), 원 단위"
    )
    spouse_duration_months: int = Field(
        default=0, ge=0, description="배우자 청약통장 가입기간 (개월). 없으면 0"
    )


class UserProfile(BaseModel):
    """자격판정과 가점계산에 쓰이는 사용자 정보."""

    age: int | None = Field(
        default=None, ge=0, description="만 나이. '30살'처럼 나이만 알려줘도 됨"
    )
    birth_date: str | None = Field(
        default=None,
        description="생년월일(YYYY-MM-DD). '951024'·'19951024'·'1995-10-24' 등 어떤 형식이든 "
        "YYYY-MM-DD로 정규화해 넣으세요(2자리 연도는 현재 기준 만 19~99세 범위로 추정). 주면 만 "
        "나이와 무주택기간(만 30세 기산)을 정확히 계산하고, 없으면 age로 근사합니다",
    )
    is_head_of_household: bool | None = Field(
        default=None, description="세대주 여부. 규제지역 1순위 판정에 필요"
    )
    residence_area: str | None = Field(
        default=None, description="주민등록상 거주 시·도 (예: 서울, 경기, 부산)"
    )
    residence_years_in_region: int | None = Field(
        default=None, ge=0, description="해당 시·도 연속 거주 연수"
    )
    homeless_duration_months: int | None = Field(
        default=None,
        ge=0,
        description="무주택 기간(개월). 유주택자는 0. birth_date를 주면 자동 계산됨",
    )
    homeless_since_date: str | None = Field(
        default=None,
        description="무주택자가 된 날(YYYY-MM-DD). 과거 주택을 처분한 적이 있으면 처분 후 무주택이 "
        "된 날. birth_date와 함께 주면 무주택기간을 '만 30세와 이 날짜 중 늦은 쪽'부터 계산합니다",
    )
    owned_house_count: int | None = Field(
        default=None,
        ge=0,
        description="세대가 보유한 주택 수(분양권·입주권 포함). 0=무주택세대. "
        "무주택 여부와 2주택 이상 세대 1순위 제한 판정에 사용(무주택기간 가점과 별개)",
    )
    owns_home_self: bool | None = Field(
        default=None,
        description="세대 보유 주택의 소유자가 본인 또는 배우자인지 여부. "
        "True면 만 60세 이상 직계존속 예외(제53조)를 적용하지 않는다",
    )
    home_owner_is_ascendant_60plus: bool | None = Field(
        default=None,
        description="세대 보유 주택의 소유자가 직계존속(부모·조부모)이고 만 60세 이상인지 여부. "
        "주택공급규칙 제53조에 따라 만 60세 이상 직계존속 소유 주택은 무주택으로 간주된다"
        "(공동명의면 소유자 전원이 60세 이상이어야 함). 노부모부양 특공·공공임대는 예외",
    )
    marriage: Marriage = Field(default_factory=Marriage)
    children_count: int | None = Field(
        default=None, ge=0, description="미성년 자녀 수 (태아·입양 포함)"
    )
    infants_count: int | None = Field(default=None, ge=0, description="만 6세 미만 영유아 수")
    has_child_under_2: bool | None = Field(
        default=None, description="만 2세 미만 자녀 존재 여부 (신생아 특공 판정)"
    )
    dependents_count: int | None = Field(
        default=None, ge=0, description="본인 제외 부양가족 수 (배우자·자녀·직계존속 포함)"
    )
    is_three_generation_household: bool = Field(
        default=False, description="3세대 이상 동거 여부 (다자녀 특공 세대구성 가점)"
    )
    is_single_parent: bool = Field(default=False, description="한부모 가구 여부")
    income_and_assets: IncomeAssets = Field(default_factory=IncomeAssets)


class ProfileDocument(BaseModel):
    """세션 스토어에 저장되는 프로필 문서 전체."""

    target_housing: TargetHousing = Field(default_factory=TargetHousing)
    user_profile: UserProfile = Field(default_factory=UserProfile)
    subscription_account: SubscriptionAccount = Field(default_factory=SubscriptionAccount)


# 필드 경로 → 클라이언트 AI가 사용자에게 던질 질문. update_my_profile / analyze 가 참조한다.
#
# core: 이 항목이 모두 차면 '잠정 판정'이 가능하다(무주택 여부·규제지역 자격 게이트·공공 1순위
#       가입기간·지역 등급·만 나이). 하나라도 비면 판정을 미루고 물어본다.
# full: core에 더해 이 항목까지 채우면 '정밀 판정'이 된다(예치금·소득·부양가족·혼인). 비어도
#       판정은 수행하되, 해당 트랙 점수를 '미확정'으로 표시하고 채우라고 안내한다(action_items).
CORE_FIELD_QUESTIONS: dict[str, str] = {
    "user_profile.age": "만 나이(또는 생년월일)가 어떻게 되세요?",
    "user_profile.residence_area": "주민등록상 거주하시는 시·도가 어디인가요? (예: 서울, 경기)",
    "user_profile.owned_house_count": (
        "현재 세대가 보유한 주택이 몇 채인가요? (분양권·입주권 포함, 없으면 0)"
    ),
    "target_housing.target_region": "청약을 노리는 지역이 어디인가요? (예: 서울 마포구, 경기 하남)",
    "subscription_account.duration_months": (
        "청약통장 가입기간이 몇 개월인가요? 통장이 없으면 0으로 알려주세요."
    ),
}

FULL_FIELD_QUESTIONS: dict[str, str] = {
    "user_profile.homeless_duration_months": (
        "무주택 기간이 몇 개월인가요? 만 30세(또는 그 전 혼인신고일)부터, 주택을 처분한 "
        "적이 있으면 처분 후 무주택자가 된 날부터 셉니다. 유주택이면 0. "
        "(생년월일을 주시면 자동으로 계산해 드려요.)"
    ),
    "user_profile.marriage.is_married": "혼인신고 기준으로 기혼이신가요?",
    "user_profile.dependents_count": (
        "본인 제외 부양가족이 몇 명인가요? 같은 주민등록표의 배우자·자녀와, 본인이 "
        "세대주로 3년 이상 함께 등재된 직계존속만 셉니다(직계존속 부부 중 한 명이라도 "
        "주택 보유 시 제외, 혼인한 자녀 제외)."
    ),
    "user_profile.income_and_assets.monthly_income_krw": (
        "가구 세전 월평균 소득이 얼마인가요? (원 단위)"
    ),
    "subscription_account.total_balance_krw": "청약통장 납입 총액(예치금)이 얼마인가요? (원 단위)",
}

# 하위호환: 종전 명칭으로 참조하는 코드/문서를 위해 core+full 합본을 남긴다.
REQUIRED_FIELD_QUESTIONS: dict[str, str] = {**CORE_FIELD_QUESTIONS, **FULL_FIELD_QUESTIONS}

# 값이 비어도 대체 필드가 있으면 '채워진 것'으로 본다 — 생년월일이 나이·무주택기간을 대신한다.
_SATISFYING_ALTERNATIVES: dict[str, tuple[str, ...]] = {
    "user_profile.age": ("user_profile.birth_date",),
    "user_profile.homeless_duration_months": ("user_profile.birth_date",),
}

# 있으면 판정 정확도가 올라가는 필드와 안내 질문.
OPTIONAL_FIELD_QUESTIONS: dict[str, str] = {
    "user_profile.birth_date": (
        "생년월일이 어떻게 되세요? (YYYY-MM-DD — 무주택기간과 만 나이를 정확히 계산합니다)"
    ),
    "user_profile.homeless_since_date": (
        "과거에 주택을 처분한 적이 있다면, 무주택자가 된 날이 언제인가요? "
        "(생년월일과 함께 무주택기간 계산에 씁니다)"
    ),
    "user_profile.owns_home_self": (
        "그 주택을 본인 또는 배우자가 소유하고 있나요? (부모 등 세대원 소유면 '아니오')"
    ),
    "user_profile.home_owner_is_ascendant_60plus": (
        "주택 소유자가 부모 등 직계존속이고 만 60세 이상인가요? "
        "(공동명의면 소유자 모두 60세 이상일 때만 '예' — 제53조 무주택 간주)"
    ),
    "user_profile.is_head_of_household": "세대주이신가요? (규제지역 1순위 판정에 필요)",
    "user_profile.residence_years_in_region": (
        "지금 거주 중인 시·도에 몇 년째 연속 거주 중이신가요?"
    ),
    "user_profile.children_count": "미성년 자녀가 몇 명인가요? (태아 포함)",
    "user_profile.infants_count": "만 6세 미만 자녀가 몇 명인가요?",
    "user_profile.has_child_under_2": "만 2세 미만 자녀가 있나요? (신생아 특별공급 대상 판정)",
    "user_profile.marriage.marriage_date": "혼인신고일이 언제인가요? (신혼부부 특공 7년 판정)",
    "user_profile.income_and_assets.is_dual_income": "맞벌이이신가요?",
    "user_profile.income_and_assets.total_real_estate_krw": (
        "세대 보유 부동산 자산이 얼마인가요? (공공분양 자산기준)"
    ),
    "user_profile.income_and_assets.car_value_krw": "자동차 가액이 얼마인가요?",
    "subscription_account.payment_count": (
        "청약통장 납입 횟수가 몇 회인가요? (공공분양 순차제에 중요)"
    ),
    "subscription_account.spouse_duration_months": (
        "배우자 청약통장 가입기간이 몇 개월인가요? (민영 가점 최대 +3점)"
    ),
    "target_housing.desired_size_sqm": "희망 전용면적이 몇 ㎡인가요? (예: 59, 84)",
    "subscription_account.account_type": (
        "청약통장 종류가 무엇인가요? (주택청약종합저축/청약저축/청약예금/청약부금, 대부분 종합저축)"
    ),
}


def _get_by_path(doc: dict, path: str) -> object | None:
    node: object = doc
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _is_present(doc: dict, path: str) -> bool:
    """path에 값이 있거나, 대체 필드(예: 생년월일)가 있으면 채워진 것으로 본다."""
    if _get_by_path(doc, path) is not None:
        return True
    return any(_get_by_path(doc, alt) is not None for alt in _SATISFYING_ALTERNATIVES.get(path, ()))


def missing_fields(
    doc: dict,
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    """(core 누락, full 누락, optional 누락) 필드 목록을 [{field, question}]로 돌려준다.

    - core 누락이 있으면 판정을 미루고 물어본다(needs_more_info).
    - core만 차고 full이 비면 '잠정 판정'을 수행한다.
    - optional은 있으면 정확도만 올라간다.
    """
    core = [
        {"field": path, "question": question}
        for path, question in CORE_FIELD_QUESTIONS.items()
        if not _is_present(doc, path)
    ]
    full = [
        {"field": path, "question": question}
        for path, question in FULL_FIELD_QUESTIONS.items()
        if not _is_present(doc, path)
    ]
    optional = [
        {"field": path, "question": question}
        for path, question in OPTIONAL_FIELD_QUESTIONS.items()
        if _get_by_path(doc, path) is None
    ]
    return core, full, optional

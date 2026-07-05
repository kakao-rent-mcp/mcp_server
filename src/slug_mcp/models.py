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


class SubscriptionAccount(BaseModel):
    """청약통장 현황. 공공은 납입횟수·인정총액, 민영은 예치금(총액)·가입기간이 핵심."""

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

    age: int | None = Field(default=None, ge=0, description="만 나이")
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
        default=None, ge=0, description="무주택 기간(개월). 유주택자는 0"
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


# 분석에 반드시 필요한 필드와, 비어 있을 때 클라이언트 AI가 사용자에게 던질 질문.
# (필드 경로, 질문) — update_my_profile / analyze_my_subscription 이 참조한다.
REQUIRED_FIELD_QUESTIONS: dict[str, str] = {
    "user_profile.age": "만 나이가 어떻게 되세요?",
    "user_profile.residence_area": "주민등록상 거주하시는 시·도가 어디인가요? (예: 서울, 경기)",
    "user_profile.homeless_duration_months": (
        "무주택 기간이 몇 개월인가요? 현재 주택을 보유 중이면 0으로 알려주세요."
    ),
    "user_profile.marriage.is_married": "혼인신고 기준으로 기혼이신가요?",
    "user_profile.dependents_count": "본인을 제외한 부양가족(배우자·자녀·부모님 등)이 몇 명인가요?",
    "user_profile.income_and_assets.monthly_income_krw": (
        "가구 세전 월평균 소득이 얼마인가요? (원 단위)"
    ),
    "subscription_account.duration_months": (
        "청약통장 가입기간이 몇 개월인가요? 통장이 없으면 0으로 알려주세요."
    ),
    "subscription_account.total_balance_krw": "청약통장 납입 총액(예치금)이 얼마인가요? (원 단위)",
    "target_housing.target_region": "청약을 노리는 지역이 어디인가요? (예: 서울 마포구, 경기 하남)",
}

# 있으면 판정 정확도가 올라가는 필드와 안내 질문.
OPTIONAL_FIELD_QUESTIONS: dict[str, str] = {
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
}


def _get_by_path(doc: dict, path: str) -> object | None:
    node: object = doc
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def missing_fields(doc: dict) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """(필수 누락, 선택 누락) 필드 목록을 [{field, question}] 형태로 돌려준다."""
    required = [
        {"field": path, "question": question}
        for path, question in REQUIRED_FIELD_QUESTIONS.items()
        if _get_by_path(doc, path) is None
    ]
    optional = [
        {"field": path, "question": question}
        for path, question in OPTIONAL_FIELD_QUESTIONS.items()
        if _get_by_path(doc, path) is None
    ]
    return required, optional

"""MCP 도구가 주고받는 입출력 스키마.

이 파일의 필드명과 설명(description)은 MCP 클라이언트(사용자 쪽 AI)가
자연어 대화에서 값을 뽑아내 채우는 근거가 되므로, 이름과 설명을 명확하게 유지한다.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class HouseCategory(StrEnum):
    """분양 공고의 대분류."""

    APT = "apt"  # 아파트
    OFFICETEL = "officetel"  # 오피스텔/도시형생활주택/생활숙박시설/민간임대
    REMAINDER = "remainder"  # 무순위/잔여세대


class MaritalStatus(StrEnum):
    """특별공급 유형 판단에 쓰이는 결혼·자녀 상태."""

    NONE = "none"  # 해당 없음 (일반공급 대상)
    PRE_NEWLYWED = "pre_newlywed"  # 예비 신혼부부 (혼인 예정)
    NEWLYWED = "newlywed"  # 신혼부부 (혼인기간 7년 이내)
    HAS_CHILD = "has_child"  # 자녀 있음 (신생아 특별공급 등)
    MULTI_CHILD = "multi_child"  # 다자녀가구 (미성년 자녀 2명 이상)


class SubscriptionAccountType(StrEnum):
    """청약통장 종류. 민영/공공주택은 자격 판정 기준이 서로 다르다."""

    PRIVATE = "private"  # 민영주택청약 — 예치금 기준으로 판정
    PUBLIC = "public"  # 공공주택청약 — 가입기간·납입횟수 기준으로 판정


class SubscriptionAccount(BaseModel):
    """사용자의 청약통장 현황."""

    account_type: SubscriptionAccountType
    joined_months_ago: int = Field(..., ge=0, description="통장 가입 후 경과 개월 수")
    payment_count: int | None = Field(
        default=None, ge=0, description="공공주택 청약용 총 납입 횟수 (민영이면 비워도 됨)"
    )
    deposit_amount_10k_won: int | None = Field(
        default=None, ge=0, description="민영주택 청약용 예치금, 단위 만원 (공공이면 비워도 됨)"
    )


class UserProfile(BaseModel):
    """자격판정과 추천에 쓰이는 사용자 정보. 청약에 필요한 항목만 담는다."""

    household_size: int = Field(..., ge=1, description="본인을 포함한 가구원 수")
    annual_household_income_10k_won: int = Field(
        ..., ge=0, description="세전 연간 가구소득 합계, 단위 만원"
    )
    real_estate_value_10k_won: int = Field(
        default=0, ge=0, description="세대가 보유한 부동산 가액 합계, 단위 만원"
    )
    vehicle_value_10k_won: int = Field(
        default=0, ge=0, description="세대가 보유한 자동차 가액 합계, 단위 만원"
    )
    has_no_house: bool = Field(..., description="무주택 세대구성원 여부")
    marital_status: MaritalStatus
    region: str = Field(..., description="희망 공급지역 시도명 (예: 서울, 경기, 부산)")
    subscription_account: SubscriptionAccount


class EligibilityResult(BaseModel):
    """단일 공급유형에 대한 자격판정 결과."""

    supply_type: str
    passed: bool
    reasons_pass: list[str] = Field(default_factory=list)
    reasons_fail: list[str] = Field(default_factory=list)
    needs_manual_review: bool = Field(
        default=False,
        description="기준값 미설정 등으로 자동판정이 불완전해 사람이 다시 확인해야 하는 경우 True",
    )

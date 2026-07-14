"""API 원본 행(대문자 코드키)을 사용자·LLM 친화 키로 정제(projection)한다.

신규 PlayMCP 규칙(result 최소화 + API 응답 원문 그대로 지양) 대응. 각 조회 도구는
여기 정의된 매핑으로 필요한 필드만 골라 의미어 키로 바꿔 반환한다.

주의: 매핑에서 제외한 원본 필드는 그대로 버려진다. **다른 도구의 입력으로 쓰이는
식별자**(예: HOUSE_MANAGE_NO → id)는 반드시 남길 것.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# (원본키, 정제키) 또는 (원본키, 정제키, 변환함수)
Field = tuple[str, str] | tuple[str, str, Callable[[Any], Any]]


def to_int(value: Any) -> int | None:
    """공공데이터 응답은 수치도 문자열로 오므로 정수로 변환한다. 실패 시 None."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def project(row: dict[str, Any], fields: list[Field]) -> dict[str, Any]:
    """원본 행에서 매핑된 필드만 골라 정제 키로 옮긴다.

    값이 없거나(None) 빈 문자열인 필드는 결과에서 생략해 크기를 줄인다.
    """
    out: dict[str, Any] = {}
    for field in fields:
        src, dst = field[0], field[1]
        cast = field[2] if len(field) > 2 else None
        value = row.get(src)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        out[dst] = cast(value) if cast else value
    return out


# 청약홈 분양공고 목록 행 → 정제 (search_housing_notices)
NOTICE_LIST_FIELDS: list[Field] = [
    ("HOUSE_MANAGE_NO", "id"),  # 후속 도구(get_notice_detail·get_competition_stats) 입력키 — 필수 유지
    ("HOUSE_NM", "name"),
    ("HOUSE_SECD_NM", "house_type"),  # APT 등
    ("HOUSE_DTL_SECD_NM", "track"),  # 국민/민영
    ("RENT_SECD_NM", "supply_type"),  # 분양주택 등
    ("SUBSCRPT_AREA_CODE_NM", "region"),
    ("HSSPLY_ADRES", "address"),
    ("TOT_SUPLY_HSHLDCO", "supply_households", to_int),
    ("RCRIT_PBLANC_DE", "announce_date"),  # 모집공고일
    ("RCEPT_BGNDE", "receipt_begin"),
    ("RCEPT_ENDDE", "receipt_end"),
    ("PRZWNER_PRESNATN_DE", "winner_date"),  # 당첨자발표일
    ("MVN_PREARNGE_YM", "move_in_month"),  # 입주예정월
    ("BSNS_MBY_NM", "provider"),  # 사업주체
    ("PBLANC_URL", "detail_url"),
]

# recommend 추천 카드용 (track·supply_households는 카드 상위 필드에 이미 있어 제외)
NOTICE_CARD_FIELDS: list[Field] = [
    ("HOUSE_MANAGE_NO", "id"),
    ("HOUSE_NM", "name"),
    ("SUBSCRPT_AREA_CODE_NM", "region"),
    ("HSSPLY_ADRES", "address"),
    ("RCEPT_BGNDE", "receipt_begin"),
    ("RCEPT_ENDDE", "receipt_end"),
    ("PRZWNER_PRESNATN_DE", "winner_date"),
    ("MVN_PREARNGE_YM", "move_in_month"),
    ("PBLANC_URL", "detail_url"),
]

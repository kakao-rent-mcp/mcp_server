"""세션 프로필을 임대 룰 엔진에 넣어 유형별 자격·순위를 판정하는 도구."""

from __future__ import annotations

from typing import Any

from .. import rental_engine
from .. import store as store_module


def analyze_my_rental(session_id: str) -> dict[str, Any]:
    """저장된 프로필로 임대주택 자격을 판정한다 (네트워크 호출 없는 순수 계산).

    수행 내용: 무주택 세대구성원·자산·자동차 차단필터 → 유형별 판정 — 영구임대(수급자
    순위제), 국민임대(소득 70%컷·통장 순위·동순위 배점), 행복주택(청년·신혼부부·고령자
    등 계층 추론), 공공임대(통장 우선/잔여공급). rental_type을 정하지 않았으면 4유형
    전부를 스크리닝해 신청 가능한 유형 목록을 돌려준다.

    기준은 마이홈포털 2026년도 일반 고시 기준의 참고 기준선(잠정 판정)이며, 단지별
    기준은 공고문이 최종이다 — 결과의 verification_notes에 따라 search_lease_notices로
    공고를 찾고 결과의 detail_url(LH 청약센터 공고 페이지) 공고문 원문을 대조한다. 무엇을 더
    입력하면 정확해지는지는 action_items로, 한 줄 결론은 headline로 함께 준다.

    Args:
        session_id: update_my_profile이 발급한 세션 ID (target_housing.track='rental' 필요)
    """
    doc = store_module.default_store.get(session_id)
    if doc is None:
        return {
            "status": "session_not_found",
            "guidance": "세션이 없거나 만료되었습니다(보관 24시간). update_my_profile로 "
            "프로필을 만든 뒤 그 session_id로 다시 호출하세요.",
        }
    if (doc.get("target_housing") or {}).get("track") != "rental":
        return {
            "status": "not_rental_track",
            "guidance": "이 도구는 임대 트랙 전용입니다. update_my_profile로 "
            "target_housing.track='rental'을 설정하거나, 분양(청약) 판정은 "
            "analyze_my_subscription을 사용하세요.",
        }
    return rental_engine.analyze_rental(doc)

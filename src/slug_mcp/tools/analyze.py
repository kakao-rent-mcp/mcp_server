"""세션 프로필을 룰 엔진에 넣어 청약 자격·가점·실현가능성을 판정하는 도구."""

from __future__ import annotations

from typing import Any

from .. import engine
from .. import store as store_module


def analyze_my_subscription(session_id: str) -> dict[str, Any]:
    """저장된 프로필로 청약 종합 분석을 수행한다 (네트워크 호출 없는 순수 계산).

    수행 내용: 자격 차단 필터(무주택·자산·규제지역 세대주) → 공공/민영 트랙 분기
    → 민영 가점(84점 만점)·공공 인정총액·특별공급(신생아/신혼부부/다자녀) 배점
    → 목표지역 예상 컷오프 대조와 실현가능성, 추천 트랙, 우회 전략까지 돌려준다.
    프로필이 부족하면 status="needs_more_info"와 물어볼 질문 목록을 돌려준다.

    Args:
        session_id: update_my_profile이 발급한 세션 ID
    """
    doc = store_module.default_store.get(session_id)
    if doc is None:
        return {
            "status": "session_not_found",
            "guidance": "세션이 없거나 만료되었습니다(보관 24시간). update_my_profile로 "
            "프로필을 만든 뒤 그 session_id로 다시 호출하세요.",
        }
    return engine.analyze(doc)

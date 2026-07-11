"""세션 프로필을 룰 엔진에 넣어 청약 자격·가점·실현가능성을 판정하는 도구."""

from __future__ import annotations

from typing import Any

from .. import engine
from .. import store as store_module


def analyze_my_subscription(session_id: str) -> dict[str, Any]:
    """저장된 프로필로 청약 종합 분석을 수행한다 (네트워크 호출 없는 순수 계산).

    수행 내용: 자격 차단 필터(무주택·자산·규제지역 세대주) → 공공/민영 트랙 분기
    → 민영 가점(84점 만점)·공공 인정총액·특별공급(신생아/신혼부부/다자녀) 배점
    → 목표지역 참고 컷오프 기준선(관측값 아님) 대조와 실현가능성(추정), 추천 트랙,
    우회 전략까지 돌려준다. 특정 단지의 실제 당첨가점·경쟁률은 recommend_housing으로 확인한다.

    core 항목(나이/거주지/주택수/목표지역/통장 가입기간)만 채우면 confidence="provisional"
    로 '잠정 판정'을 돌려주고, 소득·예치금 등 full 항목을 채우면 정밀 판정이 된다. 무엇을 더
    입력하면 정확해지는지는 action_items로, 한 줄 결론은 headline로 함께 준다. core가 비면
    status="needs_more_info"와 물어볼 질문 목록을 돌려준다. 생년월일(birth_date)을 주면 만
    나이·무주택기간을 정확히 계산한다.

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

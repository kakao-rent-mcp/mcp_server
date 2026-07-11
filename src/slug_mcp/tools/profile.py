"""사용자 프로필 설정·조회 도구.

사용자가 대화 몇 번에 걸쳐 나이·소득·통장 정보를 조각조각 말하면, 클라이언트 AI가
update_my_profile을 반복 호출해 세션에 누적한다. 응답의 next_questions가
"다음에 물어볼 것"을 알려주므로, 짧은 대화만으로 분석 준비가 끝난다.

저장소는 인메모리 문서 스토어(store.py)다 — 배포 환경에 외부 DB를 붙일 수 없어
프로세스 메모리에 TTL을 걸어 보관하며, 서버 재시작 시 사라진다.
"""

from __future__ import annotations

from typing import Any

from .. import store as store_module
from ..models import (
    ProfileDocument,
    SubscriptionAccount,
    TargetHousing,
    UserProfile,
    missing_fields,
)


def _profile_status(session_id: str, doc: dict[str, Any]) -> dict[str, Any]:
    core, full, optional = missing_fields(doc)
    # 문서를 스키마에 통과시켜 기본값까지 채운 전체 모습을 돌려준다.
    full_profile = ProfileDocument.model_validate(doc).model_dump()
    # 물어볼 순서: core(잠정 판정에 필수) → full(정밀 판정) → optional(정확도).
    if core:
        next_questions = [item["question"] for item in core[:3]]
    elif full:
        next_questions = [item["question"] for item in full[:3]]
    else:
        next_questions = [item["question"] for item in optional[:2]]

    if core:
        guidance = (
            "next_questions를 사용자에게 물어 답을 받은 뒤, 같은 session_id로 update_my_profile을 "
            "다시 호출해 채워 넣으세요. core 항목이 다 차면 잠정 판정이 가능합니다."
        )
    elif full:
        guidance = (
            "잠정 판정은 지금도 가능합니다(analyze_my_subscription). next_questions까지 채우면 "
            "예치금·소득·가점을 포함한 정밀 판정이 됩니다."
        )
    else:
        guidance = (
            "필수·권장 항목이 모두 채워졌습니다. analyze_my_subscription 또는 recommend_housing을 "
            "이 session_id로 호출하세요."
        )
    return {
        "session_id": session_id,
        "profile": full_profile,
        # core+full이 모두 차야 정밀 판정 준비 완료. core만 차면 잠정 판정 가능.
        "ready_for_analysis": not core and not full,
        "ready_for_provisional": not core,
        "missing_required_fields": core,
        "missing_recommended_fields": full,
        "missing_optional_fields": optional,
        "next_questions": next_questions,
        "guidance": guidance,
    }


def update_my_profile(
    session_id: str | None = None,
    target_housing: TargetHousing | None = None,
    user_profile: UserProfile | None = None,
    subscription_account: SubscriptionAccount | None = None,
    reset: bool = False,
) -> dict[str, Any]:
    """사용자 프로필을 세션에 저장·부분 갱신한다. 청약 분석·추천의 첫 단계.

    대화에서 파악한 값만 채워서 여러 번 호출하면 서버가 누적 병합한다.
    첫 호출은 session_id 없이 하고, 응답의 session_id를 이후 모든 호출에 재사용한다.
    응답의 next_questions에 아직 부족한 정보를 물어볼 질문이 들어 있다.
    금액은 전부 원(KRW) 단위. reset=true면 세션을 비우고 새로 시작한다.

    Args:
        session_id: 기존 세션 ID (첫 호출이면 비움)
        target_housing: 희망 지역·전용면적·강제매칭 여부
        user_profile: 나이·거주지·무주택기간·혼인·가족·소득·자산 정보
        subscription_account: 청약통장 가입기간·납입횟수·납입총액(예치금)
        reset: true면 세션 프로필을 비우고 다시 시작
    """
    store = store_module.default_store
    if reset and session_id:
        store.delete(session_id)

    patch: dict[str, Any] = {}
    if target_housing is not None:
        patch["target_housing"] = target_housing.model_dump(exclude_unset=True)
    if user_profile is not None:
        patch["user_profile"] = user_profile.model_dump(exclude_unset=True)
    if subscription_account is not None:
        patch["subscription_account"] = subscription_account.model_dump(exclude_unset=True)

    sid, doc = store.upsert(session_id, patch)
    return _profile_status(sid, doc)


def get_my_profile(session_id: str) -> dict[str, Any]:
    """세션에 저장된 프로필과 완성도(부족한 항목·다음 질문)를 조회한다.

    Args:
        session_id: update_my_profile이 발급한 세션 ID
    """
    store = store_module.default_store
    doc = store.get(session_id)
    if doc is None:
        return {
            "found": False,
            "guidance": "세션이 없거나 만료되었습니다(보관 24시간). update_my_profile로 "
            "프로필을 다시 만들어 주세요.",
        }
    return {"found": True, **_profile_status(session_id, doc)}

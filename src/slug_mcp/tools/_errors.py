"""조회 도구의 외부 API/네트워크 예외를 사용자용 안내 텍스트로 정제한다.

신규 PlayMCP 규칙: tool result가 error인 경우 원본 예외/응답을 그대로 노출하지 말고
정제된 텍스트를 돌려준다. 예상 가능한 외부 실패만 삼켜 {status:"error", message}
형태로 반환하고, 그 외 예외(프로그래밍 오류)는 전파해 버그를 숨기지 않는다.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from ..clients.lh import LhConfigError
from ..clients.odcloud import OdcloudConfigError

# 삼켜서 정제 메시지로 바꿀 '예상 가능한 외부 실패' 예외들.
# httpx.HTTPError는 HTTPStatusError(4xx/5xx)·RequestError(네트워크/타임아웃)의 상위.
_HANDLED: tuple[type[Exception], ...] = (
    OdcloudConfigError,
    LhConfigError,
    httpx.HTTPError,
    ValueError,  # 지역명 등 사용자 입력 검증 실패 (_resolve_region_code)
)


def _friendly_message(exc: Exception) -> str:
    if isinstance(exc, (OdcloudConfigError, LhConfigError)):
        return "서버에 공공데이터 서비스키가 설정되어 있지 않습니다. 관리자에게 문의하세요."
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code >= 500:
            return (
                "공공데이터 제공 서버가 일시적으로 응답하지 않습니다. "
                "잠시 후 다시 시도해 주세요."
            )
        if code == 429:
            return "요청이 많아 잠시 제한되었습니다. 잠시 후 다시 시도해 주세요."
        return "요청 조건이 올바르지 않아 공고를 조회하지 못했습니다. 입력값을 확인해 주세요."
    if isinstance(exc, httpx.RequestError):
        return (
            "공공데이터 서버 연결에 실패했습니다(네트워크 오류·시간초과). "
            "잠시 후 다시 시도해 주세요."
        )
    if isinstance(exc, ValueError):
        # 지역명 오류 등 — 이미 안내형 한글 메시지라 그대로 전달한다.
        return str(exc)
    return "조회 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."


def refine_errors(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """외부 API 조회 도구를 감싸, 예상 가능한 실패를 정제된 에러 result로 바꾼다."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except _HANDLED as exc:
            return {"status": "error", "message": _friendly_message(exc)}

    return wrapper

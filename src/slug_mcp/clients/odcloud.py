"""한국부동산원 청약홈 계열(odcloud.kr) API 클라이언트.

이 계열 API는 요청 파라미터를 라이브러리가 자동으로 URL 인코딩하므로,
서비스키는 디코딩(raw) 형태인 DECODING_KEY를 그대로 넘긴다.
"""

from __future__ import annotations

import asyncio
import os

import httpx

BASE_URL = "https://api.odcloud.kr/api"

# 일시적 5xx·네트워크 오류에 대한 재시도. GET(멱등)만 부르므로 재시도가 안전하다.
# 4xx(잘못된 요청·키)는 재시도해도 소용없으니 즉시 실패시킨다.
_RETRY_BACKOFF_SECONDS = (0.5, 1.5)


class OdcloudConfigError(RuntimeError):
    """서비스키 등 필수 설정이 없을 때 발생한다."""


def _decoding_key() -> str:
    key = os.environ.get("DECODING_KEY")
    if not key:
        raise OdcloudConfigError(
            "환경변수 DECODING_KEY가 설정되어 있지 않습니다. "
            "공공데이터포털에서 발급받은 디코딩 서비스키를 설정하세요."
        )
    return key


async def get(service: str, operation: str, **params: str | int | float | bool) -> dict:
    """odcloud API를 호출한다.

    Args:
        service: 서비스명 (예: ApplyhomeInfoDetailSvc)
        operation: 오퍼레이션명 (예: getAPTLttotPblancDetail)
        **params: page, perPage, cond[FIELD::OP] 등 쿼리 파라미터
    """
    url = f"{BASE_URL}/{service}/v1/{operation}"
    query: dict[str, str | int | float | bool] = {
        "page": 1,
        "perPage": 10,
        **params,
        "serviceKey": _decoding_key(),
    }
    last_exc: Exception | None = None
    for attempt in range(len(_RETRY_BACKOFF_SECONDS) + 1):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(url, params=query)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500:
                raise  # 4xx는 재시도 무의미
            last_exc = exc
        except httpx.RequestError as exc:  # 연결·타임아웃 등 네트워크 오류
            last_exc = exc
        if attempt < len(_RETRY_BACKOFF_SECONDS):
            await asyncio.sleep(_RETRY_BACKOFF_SECONDS[attempt])
    assert last_exc is not None
    raise last_exc

"""한국부동산원 청약홈 계열(odcloud.kr) API 클라이언트.

이 계열 API는 요청 파라미터를 라이브러리가 자동으로 URL 인코딩하므로,
서비스키는 디코딩(raw) 형태인 DECODING_KEY를 그대로 넘긴다.
"""

from __future__ import annotations

import os

import httpx

BASE_URL = "https://api.odcloud.kr/api"


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
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(url, params=query)
        response.raise_for_status()
        return response.json()

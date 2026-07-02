"""한국토지주택공사(LH) API 클라이언트 (apis.data.go.kr/B552555).

이 계열 API는 공공데이터포털이 이미 URL 인코딩해 발급한 ENCODING_KEY를
그대로(재인코딩 없이) 쿼리스트링에 붙여야 정상 동작한다. 그래서 다른 파라미터는
urlencode로 인코딩하고, serviceKey만 별도로 뒤에 붙인다.

주의: lhLeaseNoticeBfhDtllInfo1(분양임대공고별 상세정보, 사전청약) 오퍼레이션은
2026-07-01 확인 시점 기준 유효한 PAN_ID로도 서버에서 HTTP 500을 반환하는 상태였다.
경로/파라미터 형식은 맞으므로(404 아님) LH 서버 측 이슈로 보이며, 재확인이 필요하다.
"""

from __future__ import annotations

import os
from urllib.parse import urlencode

import httpx

BASE_URL = "https://apis.data.go.kr/B552555"


class LhConfigError(RuntimeError):
    """서비스키 등 필수 설정이 없을 때 발생한다."""


def _encoding_key() -> str:
    key = os.environ.get("ENCODING_KEY")
    if not key:
        raise LhConfigError(
            "환경변수 ENCODING_KEY가 설정되어 있지 않습니다. "
            "공공데이터포털에서 발급받은 인코딩 서비스키를 설정하세요."
        )
    return key


async def get(service: str, **params: object) -> dict:
    """LH API를 호출한다.

    Args:
        service: 서비스/오퍼레이션명 (예: lhLeaseNoticeInfo1). LH API는 경로가
            `{service}/{service}` 형태로 한 번 더 반복된다.
        **params: PG_SZ, PAGE, PAN_ID 등 쿼리 파라미터 (serviceKey 제외)
    """
    query_string = urlencode(params)
    url = f"{BASE_URL}/{service}/{service}?{query_string}&serviceKey={_encoding_key()}"
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()

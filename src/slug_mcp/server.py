"""FastMCP 서버 진입점: 청약 정보 조회 및 추천 도구 등록.

로컬 개발(예: Claude Desktop)에서는 기본 stdio transport로 실행되고,
Docker처럼 네트워크 너머에서 접속해야 하는 환경에서는
MCP_TRANSPORT 환경변수로 http 계열 transport를 지정한다.

PlayMCP 심사 규격에 맞춰 (1) 서버/도구 이름에 'kakao'를 쓰지 않고,
(2) 모든 도구에 annotations 5종을 지정하며, (3) description에 서비스명을
국문·영문 병기로 포함하고, (4) http는 stateless(no session)로 띄운다.
"""

from __future__ import annotations

import os
import textwrap
from collections.abc import Callable
from typing import Any, Literal, cast, get_args

from dotenv import load_dotenv
from fastmcp import FastMCP

from .tools import analyze, competition, lh_lease, notices, profile, recommend

# .env의 서비스키(DECODING_KEY/ENCODING_KEY 등)를 프로세스 환경변수로 로드한다.
# 이미 설정된 환경변수는 덮어쓰지 않는다(override=False 기본값).
load_dotenv()

_Transport = Literal["stdio", "http", "sse", "streamable-http"]
_VALID_TRANSPORTS = get_args(_Transport)

# PlayMCP: description에 국문·영문 병기 고유명사로 포함해야 하는 서비스명.
SERVICE_NAME = "슬러그(Slug)"

mcp = FastMCP(
    name="slug-mcp",
    instructions=(
        f"{SERVICE_NAME}: 한국 주택 청약 공고를 검색하고, 사용자의 소득·자산·가족구성·"
        "청약통장 정보로 자격·가점을 판정해 당첨 가능성이 높은 공고를 추천합니다. "
        "권장 사용 흐름: (1) 대화에서 파악한 사용자 정보를 update_my_profile로 저장하고 "
        "응답의 next_questions로 부족한 정보를 물어 채운다 → (2) 같은 session_id로 "
        "analyze_my_subscription(종합 판정) 또는 recommend_housing(공고 추천)을 호출한다. "
        "임대주택(영구·국민·행복·공공임대) 상담이면 update_my_profile에 "
        "target_housing.track='rental'(+rental_type)을 채우세요 — 임대에 맞는 질문이 "
        "안내됩니다. 임대 공고 검색·공고문 원문은 search_lease_notices/"
        "extract_lease_notice_text를 사용합니다. "
        "금액 단위는 모두 원(KRW)이며 필드명에 _krw가 붙습니다. "
        "프로필은 서버 메모리에 24시간만 보관됩니다."
    ),
)

# (함수, 제목, annotations) — PlayMCP는 title/readOnlyHint/destructiveHint/
# openWorldHint/idempotentHint 5종을 모두 명시할 것을 요구한다.
# 조회·계산 도구는 readOnly=True, 외부 공공데이터 API를 부르면 openWorld=True.
# update_my_profile만 세션 상태를 바꾸므로 readOnly=False (병합 갱신이라 파괴적이지 않음).
_READ_EXTERNAL: dict[str, Any] = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
_READ_LOCAL: dict[str, Any] = {**_READ_EXTERNAL, "openWorldHint": False}
_WRITE_LOCAL: dict[str, Any] = {**_READ_LOCAL, "readOnlyHint": False}

_TOOLS: tuple[tuple[Callable[..., Any], str, dict[str, Any]], ...] = (
    (notices.search_housing_notices, "분양 공고 검색", _READ_EXTERNAL),
    (notices.get_notice_detail, "분양 공고 상세 조회", _READ_EXTERNAL),
    (lh_lease.search_lease_notices, "LH 분양·임대 공고 검색", _READ_EXTERNAL),
    (lh_lease.get_lease_notice_detail, "LH 공고 상세 조회", _READ_EXTERNAL),
    (lh_lease.extract_lease_notice_text, "LH 공고문 원문 추출", _READ_EXTERNAL),
    (competition.get_competition_stats, "경쟁률·당첨가점 조회", _READ_EXTERNAL),
    (profile.update_my_profile, "내 프로필 저장·갱신", _WRITE_LOCAL),
    (profile.get_my_profile, "내 프로필 조회", _READ_LOCAL),
    (analyze.analyze_my_subscription, "청약 종합 판정", _READ_LOCAL),
    (recommend.recommend_housing, "맞춤 청약 추천", _READ_EXTERNAL),
)


def _describe(fn: Callable[..., Any]) -> str:
    """docstring 앞에 서비스명을 붙여 PlayMCP description 규칙을 만족시킨다."""
    body = textwrap.dedent(fn.__doc__ or "").strip()
    return f"[{SERVICE_NAME}] {body}"


for _fn, _title, _annotations in _TOOLS:
    mcp.tool(
        _fn,
        description=_describe(_fn),
        annotations={"title": _title, **_annotations},
    )


def main() -> None:
    transport_raw = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport_raw not in _VALID_TRANSPORTS:
        raise ValueError(
            f"알 수 없는 MCP_TRANSPORT 값입니다: {transport_raw!r}. "
            f"다음 중 하나여야 합니다: {_VALID_TRANSPORTS}"
        )
    transport = cast(_Transport, transport_raw)

    if transport == "stdio":
        mcp.run()
    else:
        # PlayMCP는 stateless(no session) 서버를 권장한다.
        mcp.run(
            transport=transport,
            host=os.environ.get("MCP_HOST", "0.0.0.0"),
            port=int(os.environ.get("MCP_PORT", "8000")),
            stateless_http=True,
        )


if __name__ == "__main__":
    main()

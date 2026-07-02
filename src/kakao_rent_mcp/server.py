"""FastMCP 서버 진입점: 청약 정보 조회 및 추천 도구 등록.

로컬 개발(예: Claude Desktop)에서는 기본 stdio transport로 실행되고,
Docker/EC2처럼 네트워크 너머에서 접속해야 하는 환경에서는
MCP_TRANSPORT 환경변수로 http 계열 transport를 지정한다.
"""

from __future__ import annotations

import os
from typing import Literal, cast, get_args

from fastmcp import FastMCP

from .tools import competition, eligibility, notices, recommend

_Transport = Literal["stdio", "http", "sse", "streamable-http"]
_VALID_TRANSPORTS = get_args(_Transport)

mcp = FastMCP(
    name="kakao-rent-mcp",
    instructions=(
        "한국 주택 청약(분양/임대) 공고를 검색하고, 사용자의 소득·자산·"
        "가족구성·청약통장 정보를 바탕으로 자격 여부를 판정해 맞는 공고를 추천합니다. "
        "금액 단위는 모두 '만원'입니다."
    ),
)

for _fn in (
    notices.search_housing_notices,
    notices.get_notice_detail,
    competition.get_competition_stats,
    eligibility.check_eligibility,
    recommend.recommend_housing,
):
    mcp.tool(_fn)


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
        mcp.run(
            transport=transport,
            host=os.environ.get("MCP_HOST", "0.0.0.0"),
            port=int(os.environ.get("MCP_PORT", "8000")),
        )


if __name__ == "__main__":
    main()

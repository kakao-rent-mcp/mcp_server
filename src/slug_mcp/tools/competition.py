"""경쟁률·당첨가점·특별공급 신청현황 조회 도구."""

from __future__ import annotations

import asyncio

from ..clients import odcloud


async def get_competition_stats(house_manage_no: str) -> dict:
    """공고의 순위별 경쟁률, 당첨 가점, 특별공급 신청현황을 함께 조회한다.

    세 조회는 서로 독립적이므로 순차로 기다리지 않고 동시에 부른다(왕복 대기 1/3).

    Args:
        house_manage_no: 공고의 주택관리번호
    """
    cond: dict[str, str | int] = {"cond[HOUSE_MANAGE_NO::EQ]": house_manage_no, "perPage": 50}
    competition, winning_scores, special_supply = await asyncio.gather(
        odcloud.get("ApplyhomeInfoCmpetRtSvc", "getAPTLttotPblancCmpet", **cond),
        odcloud.get("ApplyhomeInfoCmpetRtSvc", "getAptLttotPblancScore", **cond),
        odcloud.get("ApplyhomeInfoCmpetRtSvc", "getAPTSpsplyReqstStus", **cond),
    )
    return {
        "competition": competition.get("data", []),
        "winning_scores": winning_scores.get("data", []),
        "special_supply": special_supply.get("data", []),
    }


async def get_competition_rates(house_manage_no: str) -> list[dict]:
    """공고의 순위별 경쟁률 행만 조회한다 (당첨가점·특공은 제외).

    유사 과거 공고의 경쟁률만 필요할 때 3콜 대신 1콜로 가볍게 가져오기 위한 헬퍼.

    Args:
        house_manage_no: 공고의 주택관리번호
    """
    cond: dict[str, str | int] = {"cond[HOUSE_MANAGE_NO::EQ]": house_manage_no, "perPage": 50}
    result = await odcloud.get("ApplyhomeInfoCmpetRtSvc", "getAPTLttotPblancCmpet", **cond)
    return result.get("data", [])

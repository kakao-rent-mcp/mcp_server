"""경쟁률·당첨가점·특별공급 신청현황 조회 도구."""

from __future__ import annotations

from ..clients import odcloud


async def get_competition_stats(house_manage_no: str) -> dict:
    """공고의 순위별 경쟁률, 당첨 가점, 특별공급 신청현황을 함께 조회한다.

    Args:
        house_manage_no: 공고의 주택관리번호
    """
    cond: dict[str, str | int] = {"cond[HOUSE_MANAGE_NO::EQ]": house_manage_no, "perPage": 50}
    competition = await odcloud.get("ApplyhomeInfoCmpetRtSvc", "getAPTLttotPblancCmpet", **cond)
    winning_scores = await odcloud.get("ApplyhomeInfoCmpetRtSvc", "getAptLttotPblancScore", **cond)
    special_supply = await odcloud.get("ApplyhomeInfoCmpetRtSvc", "getAPTSpsplyReqstStus", **cond)
    return {
        "competition": competition.get("data", []),
        "winning_scores": winning_scores.get("data", []),
        "special_supply": special_supply.get("data", []),
    }

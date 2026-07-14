"""경쟁률·당첨가점·특별공급 신청현황 조회 도구."""

from __future__ import annotations

import asyncio

from ..clients import odcloud
from ._projection import COMPETITION_FIELDS, SCORE_FIELDS, project


async def get_competition_stats(house_manage_no: str) -> dict:
    """공고의 순위별 경쟁률, 당첨 가점, 특별공급 신청현황을 함께 조회한다.

    세 조회는 서로 독립적이므로 순차로 기다리지 않고 동시에 부른다(왕복 대기 1/3).
    competition은 house_type·rank·residence·competition_rate 등, winning_scores는
    house_type·lowest/average/top_score로 정제해 돌려준다(원본 코드필드 제외).

    Args:
        house_manage_no: 공고의 주택관리번호 (search_housing_notices 결과의 id)
    """
    cond: dict[str, str | int] = {"cond[HOUSE_MANAGE_NO::EQ]": house_manage_no, "perPage": 50}
    competition, winning_scores, special_supply = await asyncio.gather(
        odcloud.get("ApplyhomeInfoCmpetRtSvc", "getAPTLttotPblancCmpet", **cond),
        odcloud.get("ApplyhomeInfoCmpetRtSvc", "getAptLttotPblancScore", **cond),
        odcloud.get("ApplyhomeInfoCmpetRtSvc", "getAPTSpsplyReqstStus", **cond),
    )
    return {
        "competition": [project(row, COMPETITION_FIELDS) for row in competition.get("data", [])],
        "winning_scores": [project(row, SCORE_FIELDS) for row in winning_scores.get("data", [])],
        # 특별공급(34필드, {지역}_{유형}_CNT 코드 조합)은 전용 변환이 필요해 후속 배치로
        # 미룬다. 현재는 원본 유지 — docs/result-refinement-audit.md 참고.
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


async def get_winning_scores(house_manage_no: str) -> list[dict]:
    """공고의 주택형별 당첨가점 행만 조회한다 (최저 LWET_SCORE·평균 AVRG_SCORE·최고 TOP_SCORE).

    민영 가점제 당첨 커트라인(최저 당첨가점)을 유사 과거 공고에서 확인할 때 쓰는 1콜 헬퍼.
    값은 문자열 가점(0~84)이고, '-'는 미집계, '0'은 미달·추첨(가점경쟁 없음)을 뜻한다.

    Args:
        house_manage_no: 공고의 주택관리번호
    """
    cond: dict[str, str | int] = {"cond[HOUSE_MANAGE_NO::EQ]": house_manage_no, "perPage": 50}
    result = await odcloud.get("ApplyhomeInfoCmpetRtSvc", "getAptLttotPblancScore", **cond)
    return result.get("data", [])

"""분양 공고 검색 및 상세 조회 도구."""

from __future__ import annotations

import asyncio

from ..clients import odcloud
from ..models import HouseCategory
from ._projection import NOTICE_LIST_FIELDS, project

_DETAIL_OPERATION_BY_CATEGORY: dict[HouseCategory, str] = {
    HouseCategory.APT: "getAPTLttotPblancDetail",
    HouseCategory.OFFICETEL: "getUrbtyOfctlLttotPblancDetail",
    HouseCategory.REMAINDER: "getRemndrLttotPblancDetail",
}


async def fetch_housing_notices(
    house_category: HouseCategory = HouseCategory.APT,
    region: str | None = None,
    page: int = 1,
    per_page: int = 10,
) -> dict:
    """분양 공고 목록 원본 응답을 그대로 돌려주는 내부 조회 함수 (MCP 미등록).

    recommend_housing이 원본 컬럼(HSSPLY_ADRES·HOUSE_DTL_SECD_NM 등)으로 자격·경쟁
    판정을 하므로, tool 경계의 정제(search_housing_notices)와 분리해 둔다.
    """
    operation = _DETAIL_OPERATION_BY_CATEGORY[house_category]
    params: dict[str, str | int] = {"page": page, "perPage": per_page}
    if region:
        params["cond[SUBSCRPT_AREA_CODE_NM::EQ]"] = region
    return await odcloud.get("ApplyhomeInfoDetailSvc", operation, **params)


async def search_housing_notices(
    house_category: HouseCategory = HouseCategory.APT,
    region: str | None = None,
    page: int = 1,
    per_page: int = 10,
) -> dict:
    """분양 공고 목록을 검색한다.

    반환: {total(조건 일치 전체 건수), count(이번 응답 건수), notices[...]}. 각 공고는
    id·name·track(국민/민영)·region·address·receipt_begin/end·winner_date·detail_url 등
    필요한 필드만 담는다(원본 코드필드는 제외). id는 get_notice_detail·
    get_competition_stats 입력으로 그대로 쓴다.

    Args:
        house_category: apt(아파트) | officetel(오피스텔 등) | remainder(무순위/잔여)
        region: 공급지역 시도명으로 좁히기 (예: 서울, 경기). 비우면 전국 대상.
        page: 페이지 번호 (1부터 시작)
        per_page: 페이지당 결과 수
    """
    raw = await fetch_housing_notices(house_category, region, page, per_page)
    notices = [project(row, NOTICE_LIST_FIELDS) for row in raw.get("data", [])]
    return {
        "total": raw.get("totalCount"),
        "count": len(notices),
        "notices": notices,
    }


async def get_notice_detail(house_manage_no: str) -> dict:
    """APT 공고 하나의 상세정보와 주택형별(평형별) 분양가·면적을 함께 조회한다.

    Args:
        house_manage_no: 공고의 주택관리번호 (search_housing_notices 결과의 id)
    """
    cond: dict[str, str | int] = {"cond[HOUSE_MANAGE_NO::EQ]": house_manage_no, "perPage": 1}
    cond_mdl: dict[str, str | int] = {"cond[HOUSE_MANAGE_NO::EQ]": house_manage_no, "perPage": 50}
    # 상세와 평형별은 서로 독립적이므로 순차로 기다리지 않고 동시에 부른다.
    detail, unit_types = await asyncio.gather(
        odcloud.get("ApplyhomeInfoDetailSvc", "getAPTLttotPblancDetail", **cond),
        odcloud.get("ApplyhomeInfoDetailSvc", "getAPTLttotPblancMdl", **cond_mdl),
    )
    return {
        "notice": detail.get("data", []),
        "unit_types": unit_types.get("data", []),
    }

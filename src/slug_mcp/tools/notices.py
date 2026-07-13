"""분양 공고 검색 및 상세 조회 도구."""

from __future__ import annotations

import asyncio

from ..clients import odcloud
from ..models import HouseCategory

_DETAIL_OPERATION_BY_CATEGORY: dict[HouseCategory, str] = {
    HouseCategory.APT: "getAPTLttotPblancDetail",
    HouseCategory.OFFICETEL: "getUrbtyOfctlLttotPblancDetail",
    HouseCategory.REMAINDER: "getRemndrLttotPblancDetail",
}


async def search_housing_notices(
    house_category: HouseCategory = HouseCategory.APT,
    region: str | None = None,
    page: int = 1,
    per_page: int = 10,
) -> dict:
    """분양 공고 목록을 검색한다.

    프로필·세션 없이 즉시 조회한다. 단순히 공고 정보를 요청하는 경우(추천이 아님)에는
    선입력을 요구하지 말고 이 도구를 바로 호출한다.

    Args:
        house_category: apt(아파트) | officetel(오피스텔 등) | remainder(무순위/잔여)
        region: 공급지역 시도명으로 좁히기 (예: 서울, 경기). 비우면 전국 대상.
        page: 페이지 번호 (1부터 시작)
        per_page: 페이지당 결과 수
    """
    operation = _DETAIL_OPERATION_BY_CATEGORY[house_category]
    params: dict[str, str | int] = {"page": page, "perPage": per_page}
    if region:
        params["cond[SUBSCRPT_AREA_CODE_NM::EQ]"] = region
    return await odcloud.get("ApplyhomeInfoDetailSvc", operation, **params)


async def get_notice_detail(house_manage_no: str) -> dict:
    """APT 공고 하나의 상세정보와 주택형별(평형별) 분양가·면적을 함께 조회한다.

    Args:
        house_manage_no: 공고의 주택관리번호 (search_housing_notices 결과의 HOUSE_MANAGE_NO)
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

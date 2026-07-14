"""LH(한국토지주택공사) 분양·임대 공고 검색 도구.

공공데이터포털 lhLeaseNoticeInfo1(분양·임대 공고 목록 조회) 오퍼레이션을 감싼다.
응답 JSON은 resHeader / dsList 등 여러 데이터셋 블록으로 나뉘어 오므로, 호출자
(사용자 쪽 AI)가 바로 쓰기 좋은 형태(청약홈 검색 도구와 동일한 반환형)로 정리해 돌려준다.

각 공고의 상세 공급조건·공고문 원문은 결과의 detail_url(LH 청약센터 공고 페이지)에서
확인한다. 과거의 상세조회(get_lease_notice_detail)·공고문 추출(extract_lease_notice_text)
도구는 상세 오퍼레이션(lhLeaseNoticeDtlInfo1)이 서비스키 활용승인이 있어도 게이트웨이에서
HTTP 403을 반환하는 문제로 제거했다 — 목록 검색만 제공한다.
"""

from __future__ import annotations

from ..clients import lh
from ..models import LH_REGION_CODES, LhNoticeType
from ._errors import refine_errors
from ._projection import LH_NOTICE_FIELDS, project

_LIST_SERVICE = "lhLeaseNoticeInfo1"

# 공고유형 → LH API 코드(UPP_AIS_TP_CD). notices._DETAIL_OPERATION_BY_CATEGORY와
# 같은 방식으로, 의미어 enum을 도구 계층에서 API 코드로 변환한다.
_NOTICE_TYPE_CODES: dict[LhNoticeType, str] = {
    LhNoticeType.LAND: "01",
    LhNoticeType.SALE_HOUSE: "05",
    LhNoticeType.LEASE_HOUSE: "06",
    LhNoticeType.HOUSING_WELFARE: "13",
    LhNoticeType.STORE: "22",
    LhNoticeType.NEWLYWED_HOPE: "39",
}


# ──────────────────────────────────────────────────────────────────────────
# 파싱 헬퍼 (네트워크 없음)
# ──────────────────────────────────────────────────────────────────────────
def _resolve_region_code(region: str) -> str:
    """시도명(예: '경기')을 LH 지역코드(CNP_CD)로 바꾼다. 이미 코드면 그대로 통과."""
    if region in LH_REGION_CODES:
        return LH_REGION_CODES[region]
    if region in LH_REGION_CODES.values():
        return region
    raise ValueError(
        f"알 수 없는 지역입니다: {region!r}. "
        f"지원 시도명: {', '.join(LH_REGION_CODES)} (또는 CNP_CD 코드값 직접 전달)"
    )


def _parse_list_response(data: object, page: int, per_page: int) -> dict:
    """목록 응답 [{'dsSch':..}, {'resHeader':[..], 'dsList':[..]}] 을 정리한다.

    청약홈(odcloud) 검색 도구의 반환형(data/totalCount/page/perPage/currentCount)과
    같은 키를 쓰고, LH 고유 메타(resultCode/responseTime)만 덧붙인다.
    """
    res_header: dict = {}
    ds_list: list = []
    for block in data if isinstance(data, list) else []:
        if not isinstance(block, dict):
            continue
        header_rows = block.get("resHeader")
        if header_rows:
            res_header = header_rows[0]
        rows = block.get("dsList")
        if isinstance(rows, list):
            ds_list = rows
    raw_total = ds_list[0].get("ALL_CNT") if ds_list else "0"
    try:
        total_count: int | str = int(raw_total)
    except (TypeError, ValueError):
        total_count = raw_total
    return {
        "page": page,
        "perPage": per_page,
        "totalCount": total_count,
        "currentCount": len(ds_list),
        "data": ds_list,
        # LH 고유 메타데이터
        "resultCode": res_header.get("SS_CODE"),
        "responseTime": res_header.get("RS_DTTM"),
    }


# ──────────────────────────────────────────────────────────────────────────
# MCP 도구
# ──────────────────────────────────────────────────────────────────────────
@refine_errors
async def search_lease_notices(
    start_date: str,
    end_date: str,
    notice_type: LhNoticeType | None = None,
    region: str | None = None,
    notice_name: str | None = None,
    notice_status: str | None = None,
    page: int = 1,
    per_page: int = 10,
) -> dict:
    """LH 분양·임대 공고 목록을 게시기간으로 검색한다.

    청약홈(odcloud) 공고와 별개로, LH가 직접 공급하는 분양주택·임대주택·토지·상가·
    신혼희망타운 공고를 조회한다. 게시일 기간(start_date~end_date)은 필수다.

    반환: {total, count, notices[...]}. 각 공고는 id·name·type·subtype·region·status·
    posted_date·closing_date·detail_url을 담는다(원본 코드필드는 제외). 공고문 원문과
    상세 공급조건(소득·자산 기준 등)은 detail_url(LH 청약센터 공고 페이지)에서 확인한다.

    Args:
        start_date: 게시일 검색 시작일 (YYYYMMDD, 예: 20200308)
        end_date: 게시일 검색 종료일 (YYYYMMDD, 예: 20200508)
        notice_type: 공고유형. 다음 값 중 하나로 넘긴다(한글 라벨도 허용): sale_house(분양주택),
            lease_house(임대주택), land(토지), housing_welfare(주거복지), store(상가),
            newlywed_hope(신혼희망타운). 비우면 전체.
        region: 공급지역 시도명 (예: 서울, 경기). 비우면 전국 대상.
        notice_name: 공고명 부분검색어
        notice_status: 공고상태 (공고중 | 접수중 | 접수마감 | 상담요청 | 정정공고중)
        page: 페이지 번호 (1부터 시작)
        per_page: 페이지당 결과 수
    """
    params: dict[str, str] = {
        "PG_SZ": str(per_page),
        "PAGE": str(page),
        "PAN_ST_DT": start_date,
        "PAN_ED_DT": end_date,
    }
    if notice_type is not None:
        params["UPP_AIS_TP_CD"] = _NOTICE_TYPE_CODES[notice_type]
    if region:
        params["CNP_CD"] = _resolve_region_code(region)
    if notice_name:
        params["PAN_NM"] = notice_name
    if notice_status:
        params["PAN_SS"] = notice_status

    data = await lh.get(_LIST_SERVICE, **params)
    parsed = _parse_list_response(data, page=page, per_page=per_page)
    notices = [project(row, LH_NOTICE_FIELDS) for row in parsed["data"]]
    return {"total": parsed["totalCount"], "count": len(notices), "notices": notices}

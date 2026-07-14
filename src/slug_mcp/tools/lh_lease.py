"""LH(한국토지주택공사) 분양·임대 공고 검색·상세·공고문 추출 도구.

공공데이터포털의 두 오퍼레이션을 감싼다.
  - lhLeaseNoticeInfo1     : 분양·임대 공고 목록 조회
  - lhLeaseNoticeDtlInfo1  : 공고별 상세정보 + 첨부파일(공고문 PDF 등)

응답 JSON은 resHeader / dsList / dsAhflInfo 등 여러 데이터셋 블록으로 나뉘어
오므로, 호출자(사용자 쪽 AI)가 바로 쓰기 좋은 형태로 정리해서 돌려준다.
원래 흩어져 있던 스크립트 로직(목록 파싱·첨부 탐색·PDF 텍스트 추출)을 한곳에 모았다.
"""

from __future__ import annotations

import httpx

from ..clients import lh
from ..models import LH_REGION_CODES, LhNoticeType
from ._projection import LH_NOTICE_FIELDS, project

_LIST_SERVICE = "lhLeaseNoticeInfo1"
_DETAIL_SERVICE = "lhLeaseNoticeDtlInfo1"
_DETAIL_OPERATION = "getLeaseNoticeDtlInfo1"

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


def _group_datasets(data: object) -> dict[str, list]:
    """상세 응답의 여러 블록을 데이터셋명(dsSupInfo 등) 기준으로 합쳐 돌려준다."""
    datasets: dict[str, list] = {}
    for block in data if isinstance(data, list) else []:
        if not isinstance(block, dict):
            continue
        for name, rows in block.items():
            if isinstance(rows, list):
                datasets.setdefault(name, []).extend(rows)
    return datasets


def find_attachments(data: object) -> list[dict]:
    """상세 응답의 dsAhflInfo(첨부파일) 데이터셋에서 실제 다운로드 항목만 뽑는다.

    반환: [{'type': 파일구분명, 'name': 첨부파일명, 'url': 다운로드URL}, ...]
    url 이 'http'로 시작하지 않는 라벨/헤더 행은 제외한다.
    """
    items: list[dict] = []
    for block in data if isinstance(data, list) else []:
        if not isinstance(block, dict):
            continue
        rows = block.get("dsAhflInfo")
        if not isinstance(rows, list):
            continue
        for row in rows:
            url = (row.get("AHFL_URL") or "").strip()
            if not url.lower().startswith("http"):
                continue
            items.append(
                {
                    "type": (row.get("SL_PAN_AHFL_DS_CD_NM") or "").strip(),
                    "name": (row.get("CMN_AHFL_NM") or "").strip(),
                    "url": url,
                }
            )
    return items


def pick_notice_pdf(attachments: list[dict]) -> dict | None:
    """첨부 목록에서 '공고문 PDF'를 우선순위로 고른다. 없으면 None.

    주의: LH 실데이터는 타입 라벨(예: '공고문(PDF)')과 실제 파일 확장자가 뒤바뀐
    경우가 있어(라벨 PDF인데 .hwp), **실제 확장자(.pdf)를 최우선**으로 판단한다.
    """
    pdfs = [a for a in attachments if a["name"].lower().endswith(".pdf")]
    # 1순위: 실제 .pdf 파일 중 공고문(파일명 또는 타입에 '공고문')
    for a in pdfs:
        if "공고문" in a["name"] or "공고문" in a["type"]:
            return a
    # 2순위: 아무 .pdf 파일
    if pdfs:
        return pdfs[0]
    # 최후: 실제 .pdf가 하나도 없으면 타입 라벨에 의존(라벨이 틀릴 수 있음)
    for a in attachments:
        if "PDF" in a["type"].upper():
            return a
    return None


def _extract_pdf_text(pdf_bytes: bytes, dedup: bool = True) -> str:
    """PDF 바이트에서 텍스트를 추출한다(PyMuPDF).

    dedup=True 면 LH 공고문 특유의 굵은 제목 글자 중복(연속 동일 라인)을 정리한다.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:  # pragma: no cover - 선택적 의존성
        raise RuntimeError(
            "PDF 텍스트 추출에는 PyMuPDF가 필요합니다.  pip install pymupdf"
        ) from exc

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        pages = [doc[i].get_text() for i in range(doc.page_count)]
    finally:
        doc.close()
    text = "\n".join(pages)

    if dedup:
        out: list[str] = []
        prev: str | None = None
        for line in text.splitlines():
            s = line.strip()
            if s and s == prev:
                continue
            out.append(line)
            prev = s
        text = "\n".join(out)
    return text


async def _download(url: str, timeout: int = 30) -> bytes:
    headers = {"User-Agent": "Mozilla/5.0"}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.content


# ──────────────────────────────────────────────────────────────────────────
# MCP 도구
# ──────────────────────────────────────────────────────────────────────────
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

    반환: {total, count, notices[...]}. 각 공고는 id·name·type·region·status·
    posted_date·closing_date·detail_url과, get_lease_notice_detail 입력에 쓰는
    supply_info_type·upper_type_code·system_div_code·detail_type_code를 담는다
    (원본 코드필드는 제외). 상세·공고문 조회 시 이 값들을 그대로 넘긴다.

    Args:
        start_date: 게시일 검색 시작일 (YYYYMMDD, 예: 20200308)
        end_date: 게시일 검색 종료일 (YYYYMMDD, 예: 20200508)
        notice_type: 공고유형 (분양주택/임대주택/토지/주거복지/상가/신혼희망타운). 비우면 전체.
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


async def get_lease_notice_detail(
    notice_id: str,
    supply_info_type: str,
    upper_type_code: str,
    system_div_code: str = "02",
    detail_type_code: str | None = None,
) -> dict:
    """LH 공고 하나의 상세 공급정보와 첨부파일 목록을 조회한다.

    필수 파라미터는 search_lease_notices 결과 항목에서 그대로 가져와 넘긴다.
    응답은 공급정보구분코드에 따라 dsSupInfo/dsSplScdl/dsCtrtPlc/dsAhflInfo 등
    여러 데이터셋으로 나뉘므로, 데이터셋명별로 묶고 첨부파일은 따로 정리해 돌려준다.

    Args:
        notice_id: 공고아이디 (PAN_ID)
        supply_info_type: 공급정보구분코드 (SPL_INF_TP_CD, 예: 분양주택 050)
        upper_type_code: 상위매물유형코드 (UPP_AIS_TP_CD, 예: 분양주택 05)
        system_div_code: 고객센터연계시스템구분코드 (CCR_CNNT_SYS_DS_CD, 기본 02)
        detail_type_code: 매물유형코드 (AIS_TP_CD, 옵션)
    """
    params: dict[str, str] = {
        "SPL_INF_TP_CD": supply_info_type,
        "CCR_CNNT_SYS_DS_CD": system_div_code,
        "PAN_ID": notice_id,
        "UPP_AIS_TP_CD": upper_type_code,
    }
    if detail_type_code:
        params["AIS_TP_CD"] = detail_type_code

    data = await lh.get(_DETAIL_SERVICE, operation=_DETAIL_OPERATION, **params)
    return {
        # datasets는 공급정보구분코드에 따라 종류(dsSupInfo/dsSplScdl/dsCtrtPlc…)와
        # 필드가 달라, 전 변형 샘플 없이 매핑하면 오라벨 위험이 커 후속 배치로 미룬다.
        # 현재는 원본 유지 — docs/result-refinement-audit.md 참고.
        "datasets": _group_datasets(data),
        "attachments": find_attachments(data),
    }


async def extract_lease_notice_text(
    notice_id: str,
    supply_info_type: str,
    upper_type_code: str,
    system_div_code: str = "02",
    detail_type_code: str | None = None,
) -> dict:
    """LH 공고의 공고문 PDF를 찾아 내려받아 본문 텍스트로 추출한다.

    상세조회로 첨부파일을 얻고, 그중 '공고문 PDF'를 골라 다운로드한 뒤
    PyMuPDF로 텍스트를 뽑는다. 소득·자산 기준 등 원문 확인이 필요할 때 쓴다.
    PDF 공고문을 못 찾으면 text=None 과 안내 메시지를 돌려준다.

    Args:
        notice_id: 공고아이디 (PAN_ID)
        supply_info_type: 공급정보구분코드 (SPL_INF_TP_CD, 예: 분양주택 050)
        upper_type_code: 상위매물유형코드 (UPP_AIS_TP_CD, 예: 분양주택 05)
        system_div_code: 고객센터연계시스템구분코드 (CCR_CNNT_SYS_DS_CD, 기본 02)
        detail_type_code: 매물유형코드 (AIS_TP_CD, 옵션)
    """
    detail = await get_lease_notice_detail(
        notice_id=notice_id,
        supply_info_type=supply_info_type,
        upper_type_code=upper_type_code,
        system_div_code=system_div_code,
        detail_type_code=detail_type_code,
    )
    attachments = detail["attachments"]
    target = pick_notice_pdf(attachments)
    if target is None:
        return {
            "attachments": attachments,
            "selected": None,
            "text": None,
            "message": "PDF 형태의 공고문 첨부를 찾지 못했습니다.",
        }

    pdf_bytes = await _download(target["url"])
    import anyio

    text = await anyio.to_thread.run_sync(_extract_pdf_text, pdf_bytes)
    return {
        "attachments": attachments,
        "selected": target,
        "byte_size": len(pdf_bytes),
        "text": text,
    }

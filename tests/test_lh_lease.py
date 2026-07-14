from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx

from slug_mcp.models import LhNoticeType
from slug_mcp.tools import lh_lease

FIXTURES = Path(__file__).parent / "fixtures"

_LIST_URL = "https://apis.data.go.kr/B552555/lhLeaseNoticeInfo1/lhLeaseNoticeInfo1"
_DETAIL_URL = "https://apis.data.go.kr/B552555/lhLeaseNoticeDtlInfo1/getLeaseNoticeDtlInfo1"


def _load(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@respx.mock
async def test_search_lease_notices_sends_params_and_parses_header():
    route = respx.get(_LIST_URL).mock(
        return_value=httpx.Response(200, json=_load("lh_lease_notice_list.json"))
    )

    result = await lh_lease.search_lease_notices(
        start_date="20200308",
        end_date="20200508",
        notice_type=LhNoticeType.SALE_HOUSE,
        region="경기",
        per_page=10,
    )

    assert route.called
    params = route.calls[0].request.url.params
    assert params["PAN_ST_DT"] == "20200308"
    assert params["PAN_ED_DT"] == "20200508"
    assert params["UPP_AIS_TP_CD"] == "05"  # notice_type enum -> code
    assert params["CNP_CD"] == "41"  # region name -> LH region code

    # 청약홈 검색 도구와 동일한 정제 반환형({total, count, notices})
    assert result["total"] == 2
    assert result["count"] == 2
    assert result["notices"][0]["id"] == "0000059187"
    assert result["notices"][0]["name"] == "고양삼송 공공분양주택"
    assert result["notices"][0]["type"] == "분양주택"
    # 원본 코드필드는 정제되어 노출되지 않는다.
    assert "PAN_ID" not in result["notices"][0]


@respx.mock
async def test_get_lease_notice_detail_groups_datasets_and_attachments():
    respx.get(_DETAIL_URL).mock(
        return_value=httpx.Response(200, json=_load("lh_lease_notice_detail.json"))
    )

    result = await lh_lease.get_lease_notice_detail(
        notice_id="0000059187",
        supply_info_type="050",
        upper_type_code="05",
    )

    assert "dsSupInfo" in result["datasets"]
    assert result["datasets"]["dsSupInfo"][0]["SPL_HHLD_CO"] == "120"
    # 라벨 행(AHFL_URL='다운로드')은 제외되고 실제 파일 2건만 남는다
    assert len(result["attachments"]) == 2
    assert all(a["url"].startswith("http") for a in result["attachments"])


def test_find_attachments_skips_label_rows():
    data = [
        {
            "dsAhflInfo": [
                {
                    "SL_PAN_AHFL_DS_CD_NM": "공고문",
                    "CMN_AHFL_NM": "다운로드",
                    "AHFL_URL": "다운로드",
                },
                {
                    "SL_PAN_AHFL_DS_CD_NM": "공고문 PDF",
                    "CMN_AHFL_NM": "n.pdf",
                    "AHFL_URL": "http://x/n.pdf",
                },
            ]
        }
    ]
    items = lh_lease.find_attachments(data)
    assert len(items) == 1
    assert items[0]["name"] == "n.pdf"


def test_pick_notice_pdf_prefers_notice_pdf():
    attachments = [
        {"type": "안내문 HWP", "name": "guide.hwp", "url": "http://x/guide.hwp"},
        {"type": "공고문 PDF", "name": "공고문.pdf", "url": "http://x/notice.pdf"},
    ]
    assert lh_lease.pick_notice_pdf(attachments)["url"] == "http://x/notice.pdf"
    assert lh_lease.pick_notice_pdf([]) is None


def test_pick_notice_pdf_trusts_real_extension_over_type_label():
    # LH 실데이터: 타입 라벨과 실제 확장자가 뒤바뀐 경우 → 실제 .pdf를 골라야 한다
    attachments = [
        {"type": "공고문(PDF)", "name": "모집공고문.hwp", "url": "http://x/a.hwp"},
        {"type": "공고문(hwp)", "name": "모집공고문.pdf", "url": "http://x/b.pdf"},
    ]
    assert lh_lease.pick_notice_pdf(attachments)["url"] == "http://x/b.pdf"


@respx.mock
async def test_extract_lease_notice_text_downloads_and_extracts(monkeypatch):
    respx.get(_DETAIL_URL).mock(
        return_value=httpx.Response(200, json=_load("lh_lease_notice_detail.json"))
    )
    respx.get("http://download.example/notice.pdf").mock(
        return_value=httpx.Response(200, content=b"%PDF-1.4 fake bytes")
    )
    # PyMuPDF 실호출 없이 추출 단계만 스텁으로 대체
    monkeypatch.setattr(
        lh_lease, "_extract_pdf_text", lambda data, dedup=True: "공고문 본문 텍스트"
    )

    result = await lh_lease.extract_lease_notice_text(
        notice_id="0000059187",
        supply_info_type="050",
        upper_type_code="05",
    )

    assert result["selected"]["url"] == "http://download.example/notice.pdf"
    assert result["text"] == "공고문 본문 텍스트"
    assert result["byte_size"] == len(b"%PDF-1.4 fake bytes")


@respx.mock
async def test_extract_lease_notice_text_without_pdf_returns_message():
    detail = [
        {
            "dsAhflInfo": [
                {
                    "SL_PAN_AHFL_DS_CD_NM": "안내문 HWP",
                    "CMN_AHFL_NM": "guide.hwp",
                    "AHFL_URL": "http://download.example/guide.hwp",
                }
            ]
        }
    ]
    respx.get(_DETAIL_URL).mock(return_value=httpx.Response(200, json=detail))

    result = await lh_lease.extract_lease_notice_text(
        notice_id="0000059187",
        supply_info_type="050",
        upper_type_code="05",
    )

    assert result["selected"] is None
    assert result["text"] is None
    assert "찾지 못" in result["message"]

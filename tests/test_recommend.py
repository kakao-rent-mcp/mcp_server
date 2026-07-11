"""recommend_housing 테스트.

세션 프로필 → 룰 엔진 분석 → 진행/예정 공고 스캔 → 같은 시군구·트랙의 마감 공고
실제 경쟁률(1순위 해당지역) 결합 → 당첨 쉬운(경쟁률 낮은) 순 추천.
- 마감된 공고는 추천에서 제외되고 비교군으로만 쓰인다.
- 확률 라벨 대신 유사 과거 경쟁률을 제시하며, 비교 표본이 없으면 그 사실을 명시한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from slug_mcp import store as store_module
from slug_mcp.clients import odcloud
from slug_mcp.models import HouseCategory
from slug_mcp.store import ProfileStore
from slug_mcp.tools import recommend

FIXTURES = Path(__file__).parent / "fixtures"

_SEARCH_URL = "https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/getAPTLttotPblancDetail"
_CMPET_URL = "https://api.odcloud.kr/api/ApplyhomeInfoCmpetRtSvc/v1/getAPTLttotPblancCmpet"
_SCORE_URL = "https://api.odcloud.kr/api/ApplyhomeInfoCmpetRtSvc/v1/getAptLttotPblancScore"

# 고정된 '오늘'(2026-07-08) 기준
_FUTURE = ("2026-07-20", "2026-07-29")  # 접수전
_PAST = ("2026-05-01", "2026-05-10")  # 마감


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _fresh_store(monkeypatch):
    monkeypatch.setattr(store_module, "default_store", ProfileStore())


@pytest.fixture(autouse=True)
def _frozen_today(monkeypatch):
    # 공고 접수일 기준 '오늘'을 고정해, 시간이 지나도 마감 판정이 흔들리지 않게 한다.
    monkeypatch.setattr(recommend, "_today_kst", lambda: "2026-07-08")


@pytest.fixture(autouse=True)
def _fast_retry(monkeypatch):
    # 5xx 재시도 대기시간을 없애 테스트를 빠르게 유지한다.
    monkeypatch.setattr(odcloud, "_RETRY_BACKOFF_SECONDS", (0.0, 0.0))


def _complete_patch(**overrides: object) -> dict:
    doc: dict = {
        "target_housing": {"target_region": "경기 고양", "desired_size_sqm": 59.0},
        "user_profile": {
            "age": 34,
            "is_head_of_household": True,
            "residence_area": "경기",
            "residence_years_in_region": 4,
            "homeless_duration_months": 72,
            "owned_house_count": 0,
            "marriage": {"is_married": True, "marriage_date": "2021-03-10"},
            "children_count": 2,
            "infants_count": 1,
            "has_child_under_2": True,
            "dependents_count": 3,
            "income_and_assets": {"monthly_income_krw": 7_500_000, "is_dual_income": True},
        },
        "subscription_account": {
            "duration_months": 72,
            "payment_count": 70,
            "total_balance_krw": 18_000_000,
            "spouse_duration_months": 36,
        },
    }
    for path, value in overrides.items():
        node = doc
        parts = path.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return doc


def _notice(
    house_manage_no: str,
    *,
    begin: str,
    end: str,
    sigungu: str = "고양시",
    track: str = "국민",
    supply: int = 1000,
    name: str = "테스트 공고",
) -> dict:
    return {
        "HOUSE_MANAGE_NO": house_manage_no,
        "PBLANC_NO": house_manage_no,
        "HOUSE_NM": name,
        "HSSPLY_ADRES": f"경기도 {sigungu} 어느동 123-4",
        "HOUSE_DTL_SECD_NM": track,
        "SUBSCRPT_AREA_CODE_NM": "경기",
        "RCEPT_BGNDE": begin,
        "RCEPT_ENDDE": end,
        "TOT_SUPLY_HSHLDCO": supply,
    }


def _search_response(*notices: dict) -> dict:
    return {
        "page": 1,
        "perPage": 100,
        "totalCount": len(notices),
        "currentCount": len(notices),
        "data": list(notices),
    }


def _cmpet_response(*rates: str) -> dict:
    """1순위·해당지역 경쟁률 행들로 구성된 경쟁률 응답을 만든다."""
    return {
        "data": [
            {
                "HOUSE_TY": "084.0000A",
                "SUBSCRPT_RANK_CODE": 1,
                "RESIDE_SENM": "해당지역",
                "SUPLY_HSHLDCO": 100,
                "REQ_CNT": "0",
                "CMPET_RATE": rate,
            }
            for rate in rates
        ]
    }


def _score_response(*lwet_scores: str, reside: str = "해당지역") -> dict:
    """주택형별 당첨가점(최저 LWET_SCORE) 행들로 구성된 당첨가점 응답을 만든다."""
    return {
        "data": [
            {
                "HOUSE_TY": "084.0000A",
                "RESIDE_SENM": reside,
                "LWET_SCORE": lwet,
                "AVRG_SCORE": lwet,
                "TOP_SCORE": lwet,
            }
            for lwet in lwet_scores
        ]
    }


async def test_recommend_requires_session():
    result = await recommend.recommend_housing("nope")
    assert result["status"] == "session_not_found"
    assert "update_my_profile" in result["guidance"]


async def test_recommend_incomplete_profile_returns_questions():
    session_id, _ = store_module.default_store.upsert(None, {"user_profile": {"age": 34}})
    result = await recommend.recommend_housing(session_id)
    assert result["status"] == "needs_more_info"


async def test_recommend_rejects_non_apt_category():
    """아파트 외 카테고리는 자격·경쟁률 기준이 달라 명시적으로 막는다."""
    session_id, _ = store_module.default_store.upsert(None, _complete_patch())
    result = await recommend.recommend_housing(session_id, house_category=HouseCategory.OFFICETEL)
    assert result["status"] == "unsupported_category"
    assert "search_housing_notices" in result["guidance"]


@respx.mock
async def test_recommend_tolerates_failed_comparable_fetch():
    """비교군 경쟁률 조회가 실패해도 추천 자체는 나오고, 누락을 알린다."""
    respx.get(_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=_search_response(
                _notice("OPEN1", begin=_FUTURE[0], end=_FUTURE[1]),
                _notice("PAST1", begin=_PAST[0], end=_PAST[1]),
            ),
        )
    )
    respx.get(_CMPET_URL).mock(return_value=httpx.Response(503))  # 계속 실패
    session_id, _ = store_module.default_store.upsert(None, _complete_patch())

    result = await recommend.recommend_housing(session_id, max_candidates_to_scan=5)

    assert result["status"] == "ok"
    assert len(result["recommendations"]) == 1  # 비교 실패해도 추천은 유지
    assert result["recommendations"][0]["comparable_competition"]["avg_competition_rate"] is None
    assert any("누락" in note for note in result["verification_notes"])


@respx.mock
async def test_recommend_regates_regulated_notice_per_location():
    """세대원이 비규제 목표를 잡아도, 규제지역(동탄) 공고는 세대주 요건으로 걸러진다."""
    respx.get(_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=_search_response(
                _notice("GOYANG", sigungu="고양시", begin=_FUTURE[0], end=_FUTURE[1]),
                _notice("DONGTAN", sigungu="화성시 동탄구", begin=_FUTURE[0], end=_FUTURE[1]),
            ),
        )
    )
    # 세대원(세대주 아님): 비규제 고양은 1순위 가능, 규제 동탄은 세대주 요건으로 불가
    session_id, _ = store_module.default_store.upsert(
        None, _complete_patch(**{"user_profile.is_head_of_household": False})
    )

    result = await recommend.recommend_housing(session_id, max_candidates_to_scan=5)

    ids = [rec["notice"]["HOUSE_MANAGE_NO"] for rec in result["recommendations"]]
    assert "GOYANG" in ids  # 비규제 → 세대원도 1순위 가능
    assert "DONGTAN" not in ids  # 규제 → 세대주 아니면 1순위 불가 → 제외
    assert result["skipped_ineligible_count"] == 1
    goyang = result["recommendations"][0]
    assert goyang["notice"]["HOUSE_MANAGE_NO"] == "GOYANG"
    assert goyang["regulated_region"] is False


@respx.mock
async def test_recommend_attaches_comparable_competition():
    """같은 시군구·트랙의 마감 공고 경쟁률(1순위 해당지역)이 추천에 붙는다."""
    respx.get(_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=_search_response(
                _notice("OPEN1", begin=_FUTURE[0], end=_FUTURE[1], supply=1200, name="고양 신규"),
                _notice("PAST1", begin=_PAST[0], end=_PAST[1], name="고양 과거"),
            ),
        )
    )
    respx.get(_CMPET_URL).mock(return_value=httpx.Response(200, json=_cmpet_response("3.79")))
    session_id, _ = store_module.default_store.upsert(None, _complete_patch())

    result = await recommend.recommend_housing(session_id, max_candidates_to_scan=5, top_n=3)

    assert result["status"] == "ok"
    assert result["total_candidates_scanned"] == 1  # 진행/예정 공고는 OPEN1 하나
    assert result["comparable_pool_notices"] == 1  # 마감 PAST1은 비교군으로만
    assert len(result["recommendations"]) == 1
    top = result["recommendations"][0]
    assert top["notice"]["HOUSE_MANAGE_NO"] == "OPEN1"
    assert top["track"] == "public"
    assert top["application_status"] == "접수전"
    assert top["supply_households"] == 1200
    comp = top["comparable_competition"]
    assert comp is not None
    assert comp["avg_competition_rate"] == 3.79
    assert comp["sample_notice_count"] == 1
    assert comp["undersubscribed_row_count"] == 0
    assert "3.79:1" in comp["summary"]
    # 확률 라벨은 더 이상 제공하지 않는다
    assert "feasibility" not in top
    assert result["analysis_summary"]["private_general_score"] == 43


@respx.mock
async def test_recommend_undersubscribed_counts_as_zero():
    """미달((△)) 공고는 평균에서 0으로 반영되고 미달 건수로 집계된다."""
    respx.get(_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=_search_response(
                _notice("OPEN1", begin=_FUTURE[0], end=_FUTURE[1]),
                _notice("PAST1", begin=_PAST[0], end=_PAST[1]),
            ),
        )
    )
    # 한 공고에 6.0 한 행 + 미달 한 행 → 평균 3.0, 미달 1건
    respx.get(_CMPET_URL).mock(
        return_value=httpx.Response(200, json=_cmpet_response("6.00", "(△5)"))
    )
    session_id, _ = store_module.default_store.upsert(None, _complete_patch())

    result = await recommend.recommend_housing(session_id, max_candidates_to_scan=5)

    comp = result["recommendations"][0]["comparable_competition"]
    assert comp["avg_competition_rate"] == 3.0
    assert comp["undersubscribed_row_count"] == 1
    assert "미달 1건" in comp["summary"]


@respx.mock
async def test_recommend_reports_no_comparable_data():
    """같은 시군구에 마감 공고가 없으면 경쟁률 대신 '왜 없는지' 사유를 담는다."""
    respx.get(_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=_search_response(
                _notice("OPEN1", sigungu="성남시", begin=_FUTURE[0], end=_FUTURE[1]),
            ),
        )
    )
    session_id, _ = store_module.default_store.upsert(None, _complete_patch())

    result = await recommend.recommend_housing(session_id, max_candidates_to_scan=5)

    assert len(result["recommendations"]) == 1
    comp = result["recommendations"][0]["comparable_competition"]
    assert comp["avg_competition_rate"] is None
    assert "성남시" in comp["reason"]
    assert "이력이 없어" in comp["reason"]


@respx.mock
async def test_recommend_ranks_easier_competition_first():
    """경쟁률 낮은(당첨 쉬운) 공고가 먼저 오도록 정렬된다."""
    respx.get(_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=_search_response(
                _notice("HARD", sigungu="고양시", begin=_FUTURE[0], end=_FUTURE[1], name="치열"),
                _notice("EASY", sigungu="용인시", begin=_FUTURE[0], end=_FUTURE[1], name="여유"),
                _notice("PAST_G", sigungu="고양시", begin=_PAST[0], end=_PAST[1]),
                _notice("PAST_Y", sigungu="용인시", begin=_PAST[0], end=_PAST[1]),
            ),
        )
    )

    def _by_notice(request: httpx.Request) -> httpx.Response:
        rate = "10.0" if "PAST_G" in str(request.url) else "1.0"
        return httpx.Response(200, json=_cmpet_response(rate))

    respx.get(_CMPET_URL).mock(side_effect=_by_notice)
    session_id, _ = store_module.default_store.upsert(None, _complete_patch())

    result = await recommend.recommend_housing(session_id, max_candidates_to_scan=5, top_n=3)

    order = [rec["notice"]["HOUSE_MANAGE_NO"] for rec in result["recommendations"]]
    assert order == ["EASY", "HARD"]  # 용인시(경쟁률 1) < 고양시(경쟁률 10)


@respx.mock
async def test_recommend_excludes_closed_notice():
    """마감 공고는 추천되지 않고 비교군 풀에만 들어간다."""
    respx.get(_SEARCH_URL).mock(
        return_value=httpx.Response(
            200, json=_search_response(_notice("PAST1", begin=_PAST[0], end=_PAST[1]))
        )
    )
    session_id, _ = store_module.default_store.upsert(None, _complete_patch())

    result = await recommend.recommend_housing(session_id, max_candidates_to_scan=5)

    assert result["status"] == "ok"
    assert result["recommendations"] == []
    assert result["total_candidates_scanned"] == 0
    assert result["comparable_pool_notices"] == 1


@respx.mock
async def test_recommend_attaches_observed_winning_score_for_private():
    """민영 추천에 같은 시군구 마감 공고의 해당지역 최저 당첨가점이 붙고 사용자 가점과 대조된다."""
    respx.get(_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=_search_response(
                _notice("OPEN1", begin=_FUTURE[0], end=_FUTURE[1], track="민영", name="민영 신규"),
                _notice("PAST1", begin=_PAST[0], end=_PAST[1], track="민영", name="민영 과거"),
            ),
        )
    )
    respx.get(_CMPET_URL).mock(return_value=httpx.Response(200, json=_cmpet_response("5.0")))
    # 해당지역 최저가점 60·50, 기타지역 72(집계 제외) → 해당지역 평균 55, 최저 50
    respx.get(_SCORE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    *_score_response("60", "50")["data"],
                    *_score_response("72", reside="기타지역")["data"],
                ]
            },
        )
    )
    session_id, _ = store_module.default_store.upsert(None, _complete_patch())

    result = await recommend.recommend_housing(session_id, max_candidates_to_scan=5, top_n=3)

    assert result["status"] == "ok"
    rec = result["recommendations"][0]
    assert rec["track"] == "private"
    ws = rec["observed_winning_score"]
    assert ws["observed_cutoff_avg"] == 55.0  # (60+50)/2, 기타지역 제외
    assert ws["observed_cutoff_min"] == 50.0
    assert ws["user_score"] == 43  # _complete_patch 민영 가점
    assert ws["gap"] == 43 - 55  # 관측 커트라인보다 12점 낮음
    assert "당첨 최저가점" in ws["summary"]


@respx.mock
async def test_recommend_public_notice_has_no_winning_score_call():
    """공공(국민) 공고는 가점 개념이 없어 당첨가점을 조회하지 않는다(블록도 없음)."""
    respx.get(_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=_search_response(
                _notice("OPEN1", begin=_FUTURE[0], end=_FUTURE[1], track="국민"),
                _notice("PAST1", begin=_PAST[0], end=_PAST[1], track="국민"),
            ),
        )
    )
    respx.get(_CMPET_URL).mock(return_value=httpx.Response(200, json=_cmpet_response("3.0")))
    score_route = respx.get(_SCORE_URL).mock(
        return_value=httpx.Response(200, json=_score_response())
    )
    session_id, _ = store_module.default_store.upsert(None, _complete_patch())

    result = await recommend.recommend_housing(session_id, max_candidates_to_scan=5)

    assert "observed_winning_score" not in result["recommendations"][0]
    assert not score_route.called  # 민영 키가 없으면 당첨가점 API를 부르지 않는다


@respx.mock
async def test_recommend_skips_public_notice_for_homeowner():
    """유주택자(공공 전체 부적격)는 '국민' 공고가 추천에서 걸러진다."""
    respx.get(_SEARCH_URL).mock(
        return_value=httpx.Response(
            200, json=_search_response(_notice("OPEN1", begin=_FUTURE[0], end=_FUTURE[1]))
        )
    )
    session_id, _ = store_module.default_store.upsert(
        None,
        _complete_patch(
            **{
                "user_profile.owned_house_count": 1,  # 유주택 → 공공 전체 부적격
                "user_profile.homeless_duration_months": 0,
                "user_profile.has_child_under_2": False,
                "user_profile.children_count": 0,
            }
        ),
    )

    result = await recommend.recommend_housing(session_id, max_candidates_to_scan=5)

    assert result["status"] == "ok"
    assert result["recommendations"] == []
    assert result["skipped_ineligible_count"] == 1

"""세션 프로필 + 룰 엔진 + 실시간 공고·경쟁률 데이터를 결합한 맞춤 추천 도구.

공고 행의 HOUSE_DTL_SECD_NM으로 트랙(국민/민영)을 나눠 자격 없는 트랙과 접수 마감
공고를 걸러내고, 각 공고와 같은 시군구·트랙의 마감 공고 실제 경쟁률을 붙여
'당첨 쉬운 순'으로 추천한다. 확률 추정 대신 실측 경쟁률만 제시한다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from .. import engine
from .. import store as store_module
from ..models import HouseCategory
from . import competition as competition_tools
from . import notices as notices_tools

_KST = timezone(timedelta(hours=9))


def _today_kst() -> str:
    """오늘 날짜(KST)를 공고 접수일 형식(YYYY-MM-DD)으로 돌려준다.

    KC 컨테이너가 UTC로 돌아도 하루가 어긋나지 않도록 한국시간으로 고정한다.
    """
    return datetime.now(_KST).strftime("%Y-%m-%d")


def _application_status(notice: dict[str, Any], today: str) -> str:
    """오늘(KST) 기준 청약 접수 상태: 접수중 | 접수전 | 마감.

    접수일(RCEPT_BGNDE~RCEPT_ENDDE)은 YYYY-MM-DD 문자열이라 사전식 비교로 판정한다.
    날짜가 비어 있으면(판정 불가) 마감으로 단정하지 않고 접수중으로 둔다.
    """
    begin = str(notice.get("RCEPT_BGNDE", ""))
    end = str(notice.get("RCEPT_ENDDE", ""))
    if end and end < today:
        return "마감"
    if begin and begin > today:
        return "접수전"
    return "접수중"


_SIDO_TOKENS = (
    "서울",
    "경기",
    "인천",
    "부산",
    "대구",
    "광주",
    "대전",
    "울산",
    "세종",
    "강원",
    "충북",
    "충남",
    "전북",
    "전남",
    "경북",
    "경남",
    "제주",
)


def _sido_of(region: str) -> str | None:
    """지역 문자열에서 공고 검색 필터(SUBSCRPT_AREA_CODE_NM)에 쓸 시·도명을 뽑는다."""
    for token in _SIDO_TOKENS:
        if token in region:
            return token
    return None


def _notice_track(notice: dict[str, Any]) -> str:
    """공고 행의 주택상세구분(국민/민영)으로 판정 트랙을 정한다."""
    detail_name = str(notice.get("HOUSE_DTL_SECD_NM", ""))
    if "국민" in detail_name or "공공" in detail_name:
        return "public"
    if "민영" in detail_name:
        return "private"
    return "unknown"


def _sigungu_of(address: str) -> str | None:
    """공고 주소에서 비교 단위 시군구를 뽑는다.

    도는 시/군(예: 고양시), 특별·광역시는 자치구(예: 강남구) 단위로 본다.
    검색 API는 시도까지만 필터하므로, 시군구 매칭은 주소로만 가능하다.
    """
    tokens = str(address).split()
    if not tokens:
        return None
    sido = tokens[0]
    if sido.endswith(("특별시", "광역시")):
        for token in tokens[1:]:
            if token.endswith(("구", "군")):
                return token
        return None
    if sido.endswith("특별자치시"):  # 세종 등 (하위 구 없음)
        return sido
    for token in tokens[1:]:
        if token.endswith(("시", "군")):
            return token
    return None


def _competition_rate_value(row: dict) -> float | None:
    """경쟁률 행에서 수치를 뽑는다. 미달((△..))은 0.0, 무효(-·빈값)는 None.

    미달은 사용자에겐 '쉬운' 공고이므로 평균에서 빼지 않고 0으로 반영한다
    (숫자만 골라 평균내면 경쟁 심한 공고만 남아 상향편향이 생긴다).
    """
    raw = str(row.get("CMPET_RATE", "")).strip()
    if not raw or raw == "-":
        return None
    if raw.startswith("(") or "△" in raw:  # 미달 표기
        return 0.0
    try:
        return float(raw)
    except ValueError:
        return None


def _aggregate_comparable_rates(rows_by_notice: list[list[dict]]) -> dict[str, Any] | None:
    """비교군 공고들의 1순위·해당지역 경쟁률을 집계한다. 표본이 없으면 None.

    rows_by_notice: 비교군 공고 각각의 경쟁률 행 목록.
    """
    values: list[float] = []
    undersubscribed = 0
    contributing_notices = 0
    for rows in rows_by_notice:
        note_values: list[float] = []
        for row in rows:
            if str(row.get("SUBSCRPT_RANK_CODE")) != "1":
                continue
            if "해당지역" not in str(row.get("RESIDE_SENM", "")):
                continue
            value = _competition_rate_value(row)
            if value is None:
                continue
            note_values.append(value)
            if value == 0.0:
                undersubscribed += 1
        if note_values:
            contributing_notices += 1
            values.extend(note_values)
    if not values:
        return None
    return {
        "avg_competition_rate": round(sum(values) / len(values), 2),
        "sample_notice_count": contributing_notices,
        "undersubscribed_row_count": undersubscribed,
    }


def _int_or_none(value: object) -> int | None:
    if isinstance(value, (int, str)):
        try:
            return int(value)
        except ValueError:
            return None
    return None


# 비교군 확보를 위해 시도 공고를 넉넉히 조회한다(최신순이라 과거 공고가 함께 딸려온다).
_COMPARABLE_POOL_SIZE = 100
# 시군구·트랙당 비교에 쓸 과거 공고 수 상한(과호출 방지). 최신순으로 이만큼만 본다.
_MAX_COMPARABLE_PER_AREA = 8
_TRACK_NAME = {"public": "국민", "private": "민영", "unknown": "구분미상"}


def _build_comparable(
    key: tuple[str, str] | None,
    track: str,
    has_comparable_notices: bool,
    aggregate: dict[str, Any] | None,
) -> dict[str, Any]:
    """추천 공고에 붙일 유사 과거 경쟁률 블록을 만든다.

    데이터가 있으면 요약 문구까지, 없으면 '왜 없는지' 사유를 담는다(빈칸 대신 설명).
    """
    if key is None:
        return {
            "avg_competition_rate": None,
            "reason": "공고 주소에서 시군구를 확인하지 못해 비교 실적을 찾지 못했습니다.",
        }
    scope = f"{key[0]}·{_TRACK_NAME.get(track, track)}"
    if aggregate is not None:
        avg = aggregate["avg_competition_rate"]
        under = aggregate["undersubscribed_row_count"]
        detail = f"1순위 해당지역 평균 {avg}:1 (표본 {aggregate['sample_notice_count']}개 공고"
        detail += f", 미달 {under}건)" if under else ")"
        summary = f"미달(신청 부족)이 잦았습니다 — {detail}" if avg < 1 else detail
        return {"scope": scope, "basis": "1순위 해당지역", "summary": summary, **aggregate}
    if not has_comparable_notices:
        reason = (
            f"같은 시군구·트랙({scope})의 마감된 공고 이력이 없어 비교할 실적이 없습니다. "
            "신도시 첫 공급이거나 해당 유형 공급이 드문 지역일 수 있습니다."
        )
    else:
        reason = f"{scope}의 과거 공고는 있으나 1순위 해당지역 경쟁률 자료가 없습니다."
    return {"scope": scope, "avg_competition_rate": None, "reason": reason}


def _winning_score_value(row: dict) -> float | None:
    """당첨가점 행에서 최저 당첨가점(LWET_SCORE)을 뽑는다. '-'/빈값은 None, 숫자는 float.

    '0'은 미달·추첨(가점 경쟁 없음)이라 0.0으로 반영한다(경쟁률의 미달 처리와 같은 철학).
    """
    raw = str(row.get("LWET_SCORE", "")).strip()
    if not raw or raw == "-":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _aggregate_comparable_scores(rows_by_notice: list[list[dict]]) -> dict[str, Any] | None:
    """비교군 공고들의 해당지역 최저 당첨가점을 집계한다. 표본이 없으면 None.

    당첨가점 응답에는 순위 컬럼이 없다(이미 최종 당첨 결과라 순위 구분 불필요) — 거주구분만 본다.
    """
    values: list[float] = []
    zero_cutoff = 0
    contributing_notices = 0
    for rows in rows_by_notice:
        note_values: list[float] = []
        for row in rows:
            if "해당지역" not in str(row.get("RESIDE_SENM", "")):
                continue
            value = _winning_score_value(row)
            if value is None:
                continue
            note_values.append(value)
            if value == 0.0:
                zero_cutoff += 1
        if note_values:
            contributing_notices += 1
            values.extend(note_values)
    if not values:
        return None
    return {
        "observed_cutoff_avg": round(sum(values) / len(values), 1),
        "observed_cutoff_min": min(values),
        "sample_notice_count": contributing_notices,
        "zero_cutoff_row_count": zero_cutoff,
    }


def _build_winning_score(
    scope: str, aggregate: dict[str, Any] | None, has_comparable: bool, user_score: int
) -> dict[str, Any]:
    """민영 추천에 붙일 유사 과거 '당첨 최저가점' 블록. 사용자 가점과 직접 대조한다."""
    if aggregate is None:
        reason = (
            f"{scope}의 마감 공고에 당첨가점 자료가 없어 비교할 커트라인이 없습니다"
            "(추첨제 물량이거나 가점 미집계일 수 있습니다)."
            if has_comparable
            else f"{scope}의 마감 공고 이력이 없어 비교할 당첨가점이 없습니다."
        )
        return {"observed_cutoff_avg": None, "reason": reason}
    avg = aggregate["observed_cutoff_avg"]
    gap = user_score - round(avg)
    verdict = (
        f"회원님 가점 {user_score}점이 관측 커트라인 평균 이상입니다(+{gap}점)."
        if gap >= 0
        else f"회원님 가점 {user_score}점이 관측 커트라인 평균보다 {-gap}점 낮습니다."
    )
    zero = aggregate["zero_cutoff_row_count"]
    summary = (
        f"{scope} 해당지역 최근 당첨 최저가점 평균 {avg}점, 최저 "
        f"{aggregate['observed_cutoff_min']:.0f}점 (표본 {aggregate['sample_notice_count']}개 공고"
        + (f", 미달·추첨 {zero}건)" if zero else ")")
        + f". {verdict}"
    )
    return {
        "scope": scope,
        "basis": "해당지역 최저 당첨가점(LWET_SCORE)",
        "user_score": user_score,
        "gap": gap,
        "summary": summary,
        **aggregate,
    }


async def recommend_housing(
    session_id: str,
    house_category: HouseCategory = HouseCategory.APT,
    max_candidates_to_scan: int = 10,
    top_n: int = 5,
) -> dict[str, Any]:
    """저장된 프로필로 진행 중인 공고를 스캔해 실현가능성 순으로 추천한다 (핵심 기능).

    동작 순서:
    1. 세션 프로필을 룰 엔진으로 분석한다 (부족하면 물어볼 질문을 돌려준다).
    2. 목표지역 시·도로 공고 후보를 수집하고, 접수 마감된 공고(신청 불가)와
       국민/민영 구분상 자격 없는 트랙(예: 유주택자의 공공분양)을 걸러낸다.
    3. 각 공고와 같은 시군구·트랙의 마감된 과거 공고들의 실제 경쟁률(1순위 해당지역)을
       붙이고, 경쟁률이 낮은(당첨 쉬운) 순으로 추천한다. 진행/예정 공고는 아직 자기
       결과가 없으므로, 확률을 지어내지 않고 '유사 과거 실적'만 제시한다.

    action_items와 headline의 추가 정보 안내는 '채우면 판정이 더 정밀해지는' 선택 항목이다.
    신청 가능한 트랙이 없더라도 결과를 먼저 제시하고, 추가 정보를 필수처럼 되묻지 말 것.

    Args:
        session_id: update_my_profile이 발급한 세션 ID
        house_category: 현재 apt(아파트)만 지원. 다른 값은 unsupported_category로 반환한다.
        max_candidates_to_scan: 검토할 공고 후보 수 상한
        top_n: 최종 추천 개수
    """
    if house_category is not HouseCategory.APT:
        # 자격판정·경쟁률 집계가 모두 아파트 청약(가점·순위제) 기준이라, 규칙이 다른
        # 오피스텔·잔여세대 등은 말없이 틀린 결과를 주는 대신 명시적으로 막는다.
        return {
            "status": "unsupported_category",
            "guidance": "recommend_housing은 아파트(apt) 청약 자격·경쟁률 분석 전용입니다. "
            "오피스텔·잔여세대 등은 자격·경쟁률 기준이 달라 추천하지 않습니다. 해당 유형 "
            "공고 목록은 search_housing_notices로 조회하세요.",
        }

    doc = store_module.default_store.get(session_id)
    if doc is None:
        return {
            "status": "session_not_found",
            "guidance": "세션이 없거나 만료되었습니다(보관 24시간). update_my_profile로 "
            "프로필을 만든 뒤 그 session_id로 다시 호출하세요.",
        }

    analysis = engine.analyze(doc)
    if analysis["status"] != "ok":
        return analysis

    scores = analysis["scores"]
    matching = analysis["matching_analysis"]
    special = scores["special_supply_scores"]

    region = matching["target_region_evaluated"]
    today = _today_kst()

    # 규제지역 여부는 공고 위치마다 다르므로(경기는 시군구 혼재), 자격을 '목표지역'이 아니라
    # '각 공고 위치'로 재판정한다. 규제 여부만 자격을 바꾸고(세대주 요건·공공 1순위 24/24)
    # 나머지 입력은 동일하니, 규제/비규제 두 경우만 계산해 캐시한다(순수 계산, 네트워크 없음).
    elig_cache: dict[bool, tuple[bool, bool]] = {}

    def _eligibility_for(region_str: str) -> tuple[bool, bool]:
        regulated = engine.is_regulated_region(region_str)
        if regulated not in elig_cache:
            per = engine.analyze(doc, region_override=region_str)
            elig = per["eligibility_status"]
            sp = per["scores"]["special_supply_scores"]
            public_ok = elig["is_eligible_for_public"] or any(
                sp[key] is not None for key in ("newborn", "newlywed", "multi_child")
            )
            elig_cache[regulated] = (public_ok, elig["is_eligible_for_private"])
        return elig_cache[regulated]

    # 시도 공고를 넉넉히 조회한다 — 최신순이라 위쪽=진행/예정, 아래쪽=비교용 과거 공고.
    search_result = await notices_tools.search_housing_notices(
        house_category=house_category,
        region=_sido_of(region),
        per_page=max(max_candidates_to_scan, _COMPARABLE_POOL_SIZE),
    )
    pool = [n for n in search_result.get("data", []) if n.get("HOUSE_MANAGE_NO")]

    # 1) 추천 후보 = 접수중·접수전(마감 제외) 중, 그 공고 위치 기준 자격을 통과한 공고.
    open_notices = [n for n in pool if _application_status(n, today) != "마감"]
    closed_notices = [n for n in pool if _application_status(n, today) == "마감"]
    scanned = open_notices[:max_candidates_to_scan]

    candidates: list[tuple[dict[str, Any], str]] = []
    skipped = 0
    for notice in scanned:
        public_ok, private_ok = _eligibility_for(notice.get("HSSPLY_ADRES") or region)
        track = _notice_track(notice)
        if (track == "public" and not public_ok) or (track == "private" and not private_ok):
            skipped += 1
            continue
        candidates.append((notice, track))

    # 2) 마감 공고를 (시군구, 트랙)별로 묶어 비교군 인덱스를 만든다(최신순, 상한 적용).
    comparable_full: dict[tuple[str, str], list[str]] = {}
    for notice in closed_notices:
        sigungu = _sigungu_of(notice.get("HSSPLY_ADRES", ""))
        if not sigungu:
            continue
        comparable_full.setdefault((sigungu, _notice_track(notice)), []).append(
            notice["HOUSE_MANAGE_NO"]
        )
    comparable_index = {k: v[:_MAX_COMPARABLE_PER_AREA] for k, v in comparable_full.items()}

    # 3) 각 후보의 (시군구, 트랙) 키를 정하고, 필요한 비교군 공고의 경쟁률을 한 번에 병렬 조회한다.
    candidate_keys: list[tuple[str, str] | None] = []
    for notice, track in candidates:
        sigungu = _sigungu_of(notice.get("HSSPLY_ADRES", ""))
        candidate_keys.append((sigungu, track) if sigungu else None)

    fetch_keys = [
        k for k in dict.fromkeys(candidate_keys) if k is not None and k in comparable_index
    ]
    unique_hmns = list(dict.fromkeys(h for key in fetch_keys for h in comparable_index[key]))

    # 당첨가점(가점제)은 민영 트랙에만 의미가 있다(공공은 납입총액 순차제) — 민영 키만 조회.
    private_fetch_keys = [k for k in fetch_keys if k[1] == "private"]
    private_hmns = list(
        dict.fromkeys(h for key in private_fetch_keys for h in comparable_index[key])
    )

    semaphore = asyncio.Semaphore(5)

    async def _rates(house_manage_no: str) -> list[dict]:
        async with semaphore:
            return await competition_tools.get_competition_rates(house_manage_no)

    async def _scores(house_manage_no: str) -> list[dict]:
        async with semaphore:
            return await competition_tools.get_winning_scores(house_manage_no)

    # 비교군 경쟁률·당첨가점은 보조 데이터라, 일부 조회가 실패해도 나머지로 계속 진행한다
    # (한 콜의 일시 오류가 추천 전체를 무너뜨리지 않게 한다).
    rate_rows, score_rows = await asyncio.gather(
        asyncio.gather(*(_rates(h) for h in unique_hmns), return_exceptions=True),
        asyncio.gather(*(_scores(h) for h in private_hmns), return_exceptions=True),
    )
    rows_by_hmn = {
        h: (rows if not isinstance(rows, BaseException) else [])
        for h, rows in zip(unique_hmns, rate_rows, strict=True)
    }
    score_by_hmn = {
        h: (rows if not isinstance(rows, BaseException) else [])
        for h, rows in zip(private_hmns, score_rows, strict=True)
    }
    failed_rate_fetches = sum(1 for rows in rate_rows if isinstance(rows, BaseException))
    aggregates = {
        key: _aggregate_comparable_rates([rows_by_hmn[h] for h in comparable_index[key]])
        for key in fetch_keys
    }
    score_aggregates = {
        key: _aggregate_comparable_scores([score_by_hmn[h] for h in comparable_index[key]])
        for key in private_fetch_keys
    }
    user_score: int = scores["private_general_score"]

    # 4) 추천 조립 — 확률 라벨 대신 유사 과거 경쟁률(+민영은 당첨 최저가점)을 붙인다.
    recommendations: list[dict[str, Any]] = []
    for (notice, track), key in zip(candidates, candidate_keys, strict=True):
        aggregate = aggregates.get(key) if key is not None else None
        has_comparable = key is not None and key in comparable_index
        regulated = engine.is_regulated_region(notice.get("HSSPLY_ADRES") or region)
        rec: dict[str, Any] = {
            "notice": notice,
            "track": track,
            "application_status": _application_status(notice, today),
            "regulated_region": regulated,
            "supply_households": _int_or_none(notice.get("TOT_SUPLY_HSHLDCO")),
            "comparable_competition": _build_comparable(key, track, has_comparable, aggregate),
        }
        if track == "private":
            scope = f"{key[0]}·민영" if key is not None else "민영"
            rec["observed_winning_score"] = _build_winning_score(
                scope,
                score_aggregates.get(key) if key is not None else None,
                has_comparable,
                user_score,
            )
        recommendations.append(rec)

    # 경쟁률 낮은(=당첨 쉬운) 순, 비교자료 없는 공고는 뒤로, 동률이면 공급세대 많은 순.
    def _rank_key(rec: dict[str, Any]) -> tuple[float, int]:
        avg = rec["comparable_competition"].get("avg_competition_rate")
        return (avg if avg is not None else float("inf"), -(rec["supply_households"] or 0))

    recommendations.sort(key=_rank_key)

    notes = list(analysis["verification_notes"])
    if failed_rate_fetches:
        notes.append(
            f"비교군 경쟁률 조회 {failed_rate_fetches}건이 일시 오류로 누락되어, 일부 추천의 "
            "과거 실적이 실제보다 적게 반영됐을 수 있습니다."
        )
    capped = [k for k in fetch_keys if len(comparable_full[k]) > _MAX_COMPARABLE_PER_AREA]
    if capped:
        areas = ", ".join(f"{s}·{_TRACK_NAME.get(t, t)}" for s, t in capped)
        notes.append(
            f"유사 과거 경쟁률은 시군구·트랙당 최신 {_MAX_COMPARABLE_PER_AREA}개 공고까지만 "
            f"반영했습니다(초과: {areas})."
        )
    notes.append(
        "유사 과거 경쟁률은 같은 시군구·트랙의 마감된 공고 실적이며, 이 공고 자체의 "
        "결과가 아닙니다. 실제 경쟁률은 공급물량·분양가·시황에 따라 달라집니다."
    )
    if private_fetch_keys:
        notes.append(
            "민영 추천의 observed_winning_score는 같은 시군구 마감 공고의 해당지역 최저 당첨가점"
            "(관측값)을 회원님 가점과 대조한 것으로, 가점제 물량에만 해당합니다. 추첨 물량·분양가·"
            "시황에 따라 실제 커트라인은 달라집니다."
        )

    return {
        "status": "ok",
        "confidence": analysis.get("confidence", "complete"),
        "headline": analysis.get("headline"),
        "total_candidates_scanned": len(scanned),
        "skipped_ineligible_count": skipped,
        "comparable_pool_notices": sum(len(v) for v in comparable_index.values()),
        "analysis_summary": {
            "private_general_score": scores["private_general_score"],
            "public_balance_recognized_krw": scores["public_balance_recognized_krw"],
            "matched_special_supplies": [
                key for key in ("newborn", "newlywed", "multi_child") if special[key] is not None
            ],
            "region_grade": matching["region_grade"],
            "recommended_tracks": matching["recommended_tracks"],
        },
        "recommendations": recommendations[:top_n],
        "action_items": analysis.get("action_items", []),
        "verification_notes": notes,
    }

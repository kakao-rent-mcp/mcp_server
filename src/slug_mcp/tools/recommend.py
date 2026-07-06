"""세션 프로필 + 룰 엔진 + 실시간 공고·경쟁률 데이터를 결합한 맞춤 추천 도구.

스펙 §8에서 지적한 갭(공고별 국민/민영 유형을 판정에 반영하지 않던 문제)을 해소:
공고 행의 HOUSE_DTL_SECD_NM으로 트랙을 나눠 자격 없는 트랙은 걸러내고,
과거 당첨가점이 조회되는 공고는 예상 컷오프를 실측값으로 보정한다(§6 권장).
"""

from __future__ import annotations

from typing import Any

from .. import engine
from .. import store as store_module
from ..models import HouseCategory
from . import competition as competition_tools
from . import notices as notices_tools

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


def _avg_competition_rate(past_competition: list[dict]) -> float:
    rates: list[float] = []
    for row in past_competition:
        try:
            rates.append(float(row.get("CMPET_RATE", 0)))
        except (TypeError, ValueError):
            continue  # 미달 등 숫자가 아닌 표기는 평균 계산에서 제외
    return sum(rates) / len(rates) if rates else 0.0


def _observed_private_cutoff(winning_scores: list[dict]) -> float | None:
    """과거 당첨가점 행에서 최저 당첨가점(진입 컷)을 추정한다. 없으면 None."""
    observed: list[float] = []
    for row in winning_scores:
        for key, value in row.items():
            if "SCORE" not in key.upper():
                continue
            try:
                observed.append(float(value))
            except (TypeError, ValueError):
                continue
    return min(observed) if observed else None


async def recommend_housing(
    session_id: str,
    house_category: HouseCategory = HouseCategory.APT,
    max_candidates_to_scan: int = 10,
    top_n: int = 5,
) -> dict[str, Any]:
    """저장된 프로필로 진행 중인 공고를 스캔해 실현가능성 순으로 추천한다 (핵심 기능).

    동작 순서:
    1. 세션 프로필을 룰 엔진으로 분석한다 (부족하면 물어볼 질문을 돌려준다).
    2. 목표지역 시·도로 공고 후보를 수집하고, 공고의 국민/민영 구분별로
       자격 없는 트랙(예: 유주택자의 공공분양)을 걸러낸다.
    3. 공고별 과거 경쟁률·당첨가점을 붙이고, 당첨가점 실측값이 있으면 예상
       컷오프를 보정해 실현가능성(Probability)을 매긴 뒤 높은 순으로 추천한다.

    Args:
        session_id: update_my_profile이 발급한 세션 ID
        house_category: apt(아파트) | officetel(오피스텔 등) | remainder(무순위/잔여)
        max_candidates_to_scan: 검토할 공고 후보 수 상한
        top_n: 최종 추천 개수
    """
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

    eligibility = analysis["eligibility_status"]
    scores = analysis["scores"]
    matching = analysis["matching_analysis"]
    special = scores["special_supply_scores"]
    # 특공 배점은 엔진이 소득·통장·자산 게이트를 통과한 경우에만 non-None이 되므로,
    # special이 채워졌다는 것은 해당 공공 특공 자격이 있다는 뜻이다(§5 파급 정합).
    public_ok = eligibility["is_eligible_for_public"] or any(
        special[key] is not None for key in ("newborn", "newlywed", "multi_child")
    )
    private_ok = eligibility["is_eligible_for_private"]

    region = matching["target_region_evaluated"]
    search_result = await notices_tools.search_housing_notices(
        house_category=house_category,
        region=_sido_of(region),
        per_page=max_candidates_to_scan,
    )
    candidates = search_result.get("data", [])

    cutoffs = matching["expected_cutoffs"]
    evaluated: list[dict[str, Any]] = []
    skipped = 0
    for notice in candidates:
        house_manage_no = notice.get("HOUSE_MANAGE_NO")
        if not house_manage_no:
            continue
        track = _notice_track(notice)
        if (track == "public" and not public_ok) or (track == "private" and not private_ok):
            skipped += 1
            continue

        stats = await competition_tools.get_competition_stats(house_manage_no)
        observed_cutoff = _observed_private_cutoff(stats["winning_scores"])
        if track == "private":
            if observed_cutoff is not None:
                pct = engine.private_feasibility_pct(
                    scores["private_general_score"],
                    int(observed_cutoff),
                    int(observed_cutoff) + 5,
                )
            else:
                pct = engine.private_feasibility_pct(
                    scores["private_general_score"],
                    cutoffs["private_score_min"],
                    cutoffs["private_score_max"],
                )
        else:
            pct = engine.public_feasibility_pct(
                scores["public_balance_recognized_krw"], cutoffs["public_balance_min_krw"]
            )

        evaluated.append(
            {
                "notice": notice,
                "track": track,
                "feasibility": engine.feasibility_label(pct),
                "feasibility_pct": pct,
                "observed_private_cutoff": observed_cutoff,
                "avg_competition_rate": round(_avg_competition_rate(stats["competition"]), 2),
                "past_competition": stats["competition"],
                "special_supply_status": stats["special_supply"],
            }
        )

    evaluated.sort(key=lambda item: (-item["feasibility_pct"], item["avg_competition_rate"]))

    return {
        "status": "ok",
        "total_candidates_scanned": len(candidates),
        "skipped_ineligible_count": skipped,
        "analysis_summary": {
            "private_general_score": scores["private_general_score"],
            "public_balance_recognized_krw": scores["public_balance_recognized_krw"],
            "matched_special_supplies": [
                key for key in ("newborn", "newlywed", "multi_child") if special[key] is not None
            ],
            "region_grade": matching["region_grade"],
            "feasibility_level": matching["feasibility_level"],
            "recommended_tracks": matching["recommended_tracks"],
        },
        "recommendations": evaluated[:top_n],
        "verification_notes": analysis["verification_notes"],
    }

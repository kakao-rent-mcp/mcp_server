"""자격판정과 실시간 공고 데이터를 결합해 맞는 청약을 추천하는 핵심 도구."""

from __future__ import annotations

from ..models import HouseCategory, UserProfile
from . import competition as competition_tools
from . import eligibility as eligibility_tools
from . import notices as notices_tools


def _avg_competition_rate(past_competition: list[dict]) -> float:
    rates: list[float] = []
    for row in past_competition:
        try:
            rates.append(float(row.get("CMPET_RATE", 0)))
        except (TypeError, ValueError):
            continue  # 미달 등 숫자가 아닌 표기는 평균 계산에서 제외
    return sum(rates) / len(rates) if rates else 0.0


async def recommend_housing(
    profile: UserProfile,
    house_category: HouseCategory = HouseCategory.APT,
    max_candidates_to_scan: int = 30,
    top_n: int = 5,
) -> dict:
    """사용자 프로필에 맞는 진행 중인 공고를 찾아 자격·경쟁력 순으로 추천한다.

    동작 순서:
    1. profile.region 기준으로 공고 후보를 최대 max_candidates_to_scan개 수집한다.
    2. 각 공고에 check_eligibility를 적용해 명백히 불합격인 후보를 제외한다.
    3. 합격/판정보류 후보에 과거 경쟁률을 붙이고, 평균 경쟁률이 낮은 순으로 top_n개를 돌려준다.

    주의: 소득기준표가 아직 채워지지 않은 상태라 소득 조건은 자동으로 걸러내지 못하고
    needs_manual_review로만 표시된다. 최종 지원 전 사람이 공고문 원문을 반드시 확인해야 한다.

    Args:
        profile: 추천 대상 사용자 정보
        house_category: apt(아파트) | officetel(오피스텔 등) | remainder(무순위/잔여)
        max_candidates_to_scan: 검토할 공고 후보 수 상한
        top_n: 최종 추천 개수
    """
    search_result = await notices_tools.search_housing_notices(
        house_category=house_category,
        region=profile.region,
        per_page=max_candidates_to_scan,
    )
    candidates = search_result.get("data", [])

    evaluated: list[dict] = []
    for notice in candidates:
        house_manage_no = notice.get("HOUSE_MANAGE_NO")
        if not house_manage_no:
            continue
        result = eligibility_tools.check_eligibility(profile)
        if not result.passed and not result.needs_manual_review:
            continue
        stats = await competition_tools.get_competition_stats(house_manage_no)
        evaluated.append(
            {
                "notice": notice,
                "eligibility": result.model_dump(),
                "past_competition": stats["competition"],
            }
        )

    evaluated.sort(key=lambda item: _avg_competition_rate(item["past_competition"]))

    return {
        "total_candidates_scanned": len(candidates),
        "eligible_or_review_needed_count": len(evaluated),
        "recommendations": evaluated[:top_n],
    }

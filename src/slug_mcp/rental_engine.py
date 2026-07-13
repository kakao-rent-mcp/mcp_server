"""임대주택(영구·국민·행복·공공) 자격 판정 엔진 — rental_rules.yaml 기반 순수 계산.

분양 engine.py와 분리한 이유: 분양은 경쟁 점수 계산(가점·납입총액), 임대는
기준표 대조 + 순위 결정으로 판정 구조가 다르다. 기준값은 일반 고시 기준이므로
판정 결과에는 항상 공고문 대조 안내를 붙인다 (docs/rental-policy-spec.md).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .engine import _age_from_birth_date, _years_since
from .models import ProfileDocument, missing_fields
from .rules import load_rental_rules

# 행복주택 계층 → 자산 상한 키 (한부모는 신혼부부와 동일 상한을 쓴다).
_HAPPY_ASSET_KEY = {
    "youth": "youth",
    "newlywed": "newlywed",
    "single_parent": "newlywed",
    "elderly": "elderly",
    "welfare_recipient": "welfare_recipient",
    "college_student": "college_student",
}

# 공공임대 수도권 구분을 위한 토큰.
_CAPITAL_TOKENS = ("서울", "경기", "인천")


def rental_income_ratio_pct(
    monthly_income_krw: int, household_size: int, rules: dict[str, Any]
) -> float | None:
    """세전 월소득이 임대용 도시근로자 월평균소득(가구원수별 개별 행)의 몇 %인지.

    분양 소득표(scoring.income_ratio_pct, 3인 이하 통합 행)와 표가 다르다 — 혼용 금지.
    8인 이상 가구는 고시 미확인이라 None(판정 불가)을 돌려준다.
    """
    table: dict[str, int] = rules["rental_income_100pct_krw"]
    baseline = table.get(str(max(1, household_size)))
    if baseline is None:
        return None
    return monthly_income_krw / baseline * 100


def income_within_cap(
    income_ratio: float | None,
    cap_pct: float | None,
    household_size: int,
    rules: dict[str, Any],
) -> bool | None:
    """유형별 소득 상한(%)에 1·2인 가구 가산(%p)을 더해 충족 여부를 본다.

    None = 판정 불가 — 상한이 없는 계층(cap_pct=None)이거나 소득표 밖(income_ratio=None).
    """
    if cap_pct is None or income_ratio is None:
        return None
    bonus = rules["household_income_bonus_pct"].get(str(max(1, household_size)), 0)
    return income_ratio <= cap_pct + bonus


def check_assets(
    rental_type: str,
    happy_tier: str | None,
    real_estate_krw: int | None,
    car_value_krw: int | None,
    rules: dict[str, Any],
) -> list[dict[str, str]]:
    """유형별 자산·자동차 상한 위반 목록. 빈 리스트 = 통과.

    수집 필드는 부동산·자동차뿐이라 '총자산' 기준은 하한 검사만 한다: 부동산만으로
    총자산 상한을 넘으면 확정 탈락이고, 넘지 않으면 통과시키되 금융자산 포함 여부는
    호출자가 공고문 대조 안내로 넘긴다.
    """
    limits_root = rules["asset_limits_10k_won"]
    if rental_type == "happy":
        limits = limits_root["happy"][_HAPPY_ASSET_KEY[happy_tier or "newlywed"]]
    else:
        limits = limits_root[rental_type]

    violations: list[dict[str, str]] = []
    asset_cap_key = "real_estate" if "real_estate" in limits else "total_asset"
    asset_cap = limits[asset_cap_key] * 10_000
    label = "부동산" if asset_cap_key == "real_estate" else "총자산"
    if real_estate_krw is not None and real_estate_krw > asset_cap:
        violations.append(
            {
                "filter": "asset",
                "reason": f"보유 부동산 {real_estate_krw:,}원이 {label} 상한 "
                f"{asset_cap:,}원을 초과합니다.",
            }
        )
    vehicle_cap = limits["vehicle"] * 10_000
    if car_value_krw is not None and car_value_krw > vehicle_cap:
        reason = (
            "이 계층은 자동차를 소유할 수 없습니다."
            if vehicle_cap == 0
            else f"자동차 가액 {car_value_krw:,}원이 상한 {vehicle_cap:,}원을 초과합니다."
        )
        violations.append({"filter": "vehicle", "reason": reason})
    return violations


def judge_permanent(
    *,
    age: int,
    income_ratio: float | None,
    household_size: int,
    is_basic_living_recipient: bool | None,
    is_national_merit: bool | None,
    is_near_poverty: bool | None,
    is_single_parent: bool,
    rules: dict[str, Any],
) -> dict[str, Any]:
    """영구임대 순위 판정 — 수급자 중심 순위제, 청약통장 불필요.

    프로필로 판정 가능한 1순위 자격만 본다. 북한이탈주민 등 필드가 없는 카테고리는
    스펙의 '뺀 것' — 해당자는 공고문 대조를 안내한다 (orchestrator의 공통 노트).
    """
    cfg = rules["permanent"]
    notes: list[str] = []
    if is_basic_living_recipient:
        return {"eligible": True, "rank": 1, "basis": "생계·의료급여 수급자", "notes": notes}
    if is_single_parent:
        notes.append(
            "영구임대 1순위인 '지원대상 한부모가족'은 한부모가족지원법상 지원대상(소득요건 "
            "있음)이어야 합니다 — 해당 여부는 공고문·주민센터에서 확인하세요."
        )
        return {
            "eligible": True,
            "rank": 1,
            "basis": "한부모가족(지원대상 여부 확인 필요)",
            "notes": notes,
        }
    merit_ok = income_within_cap(
        income_ratio, cfg["rank1"]["national_merit_income_pct"], household_size, rules
    )
    if is_national_merit and merit_ok:
        return {"eligible": True, "rank": 1, "basis": "국가유공자(소득 70% 이하)", "notes": notes}
    if is_near_poverty and age >= 65:
        return {
            "eligible": True,
            "rank": 1,
            "basis": "만 65세 이상 수급권자·차상위",
            "notes": notes,
        }
    if income_within_cap(income_ratio, cfg["rank2"]["income_pct"], household_size, rules):
        notes.append(
            "영구임대 2순위의 1·2인 가구 소득 가산은 고시 표기가 엇갈려 공통 가산"
            "(+20/+10%p)을 적용했습니다 — 공고문 기준을 확인하세요."
        )
        return {"eligible": True, "rank": 2, "basis": "소득 50% 이하(가산 반영)", "notes": notes}
    if income_ratio is None:
        notes.append("소득표 밖(8인 이상 가구 등)이라 소득요건은 공고문으로 확인해야 합니다.")
        return {
            "eligible": False,
            "rank": None,
            "basis": "수급·한부모·유공자 자격이 없고 소득요건은 판정할 수 없습니다(소득표 밖).",
            "notes": notes,
        }
    return {
        "eligible": False,
        "rank": None,
        "basis": "수급·한부모·유공자 자격이 없고 소득이 2순위 상한을 초과합니다.",
        "notes": notes,
    }


def _bracket_points(bracket: dict[int, int], value: int) -> int:
    """{하한: 점수} 표에서 value가 충족하는 최고 점수를 고른다 (미달이면 0)."""
    return max((pts for threshold, pts in bracket.items() if value >= threshold), default=0)


def national_tiebreak_score(
    *,
    age: int,
    dependents_count: int,
    residence_years: int,
    children_count: int,
    payment_count: int,
    rules: dict[str, Any],
) -> dict[str, Any]:
    """국민임대 동일순위 경쟁 시 배점. 고령부양 항목은 프로필 미수집이라 0점 처리."""
    table = rules["national"]["tiebreak_score_table"]
    score = {
        "age": _bracket_points(table["age"], age),
        "dependents": _bracket_points(table["dependents"], dependents_count),
        "residence_years": _bracket_points(table["residence_years"], residence_years),
        "minor_children": _bracket_points(table["minor_children"], children_count),
        "payment_count": _bracket_points(table["payment_count"], payment_count),
        "elderly_care": 0,
    }
    notes = [
        "고령부양 배점(65세 이상 직계존속 1년 이상 부양, 3점)은 수집 항목이 아니라 "
        "0점으로 두었습니다 — 해당되면 실제 배점이 3점 높습니다."
    ]
    return {**score, "total": sum(score.values()), "notes": notes}


def judge_national(
    *,
    income_ratio: float | None,
    household_size: int,
    desired_size_sqm: float | None,
    account_months: int,
    payment_count: int,
    age: int,
    dependents_count: int,
    residence_years: int,
    children_count: int,
    rules: dict[str, Any],
) -> dict[str, Any]:
    """국민임대: 소득컷(70%) → 면적별 순위(50㎡ 미만 거주지 / 이상 통장) → 동순위 배점."""
    cfg = rules["national"]
    notes: list[str] = []
    tiebreak = national_tiebreak_score(
        age=age,
        dependents_count=dependents_count,
        residence_years=residence_years,
        children_count=children_count,
        payment_count=payment_count,
        rules=rules,
    )
    income_ok = income_within_cap(income_ratio, cfg["income_pct"], household_size, rules)
    if income_ok is False:
        return {
            "eligible": False,
            "rank": None,
            "basis": f"가구 소득이 국민임대 상한({cfg['income_pct']}% + 1·2인 가산)을 초과합니다.",
            "notes": notes,
            "tiebreak": tiebreak,
        }
    if income_ok is None:
        notes.append("소득표 밖(8인 이상 가구 등)이라 소득요건은 공고문으로 확인해야 합니다.")

    if desired_size_sqm is not None and desired_size_sqm < 50:
        priority_ok = income_within_cap(
            income_ratio, cfg["income_pct_priority_under_50sqm"], household_size, rules
        )
        if priority_ok:
            notes.append("소득 50% 이하 — 전용 50㎡ 미만 우선공급 대상입니다.")
        notes.append(
            "전용 50㎡ 미만은 거주 시·군·구로 순위가 갈립니다(당해 1순위/연접 2순위/기타 "
            "3순위) — 거주지가 공고 지역과 같은 시·군인지 공고문으로 확인하세요."
        )
        return {
            "eligible": True,
            "rank": None,
            "basis": "소득요건 충족(50㎡ 미만, 거주지 순위)",
            "notes": notes,
            "tiebreak": tiebreak,
        }

    if desired_size_sqm is None:
        notes.append("희망 전용면적 미입력 — 50㎡ 이상(통장 순위) 기준으로 판정했습니다.")
    rank_cfg = cfg["rank_50sqm_or_more"]
    if (
        account_months >= rank_cfg["rank1"]["account_months"]
        and payment_count >= rank_cfg["rank1"]["payment_count"]
    ):
        rank = 1
    elif (
        account_months >= rank_cfg["rank2"]["account_months"]
        and payment_count >= rank_cfg["rank2"]["payment_count"]
    ):
        rank = 2
    else:
        rank = 3
    return {
        "eligible": True,
        "rank": rank,
        "basis": f"소득요건 충족, 통장 기준 {rank}순위",
        "notes": notes,
        "tiebreak": tiebreak,
    }


def infer_happy_tiers(
    *,
    age: int,
    is_married: bool | None,
    marriage_years: float | None,
    infants_count: int,
    is_single_parent: bool,
    is_housing_benefit_recipient: bool | None,
) -> list[str]:
    """프로필로 추론 가능한 행복주택 계층 목록 (우선순위순).

    대학생·취업준비생·사회초년생·산업단지근로자는 재학·재직 필드가 없어 추론하지
    않는다 — 스펙의 '뺀 것'. 신혼부부는 '혼인 7년 이내 OR 6세 이하 자녀' OR 조건.
    """
    cfg_years = 7  # rental_rules.yaml happy.tiers.newlywed.marriage_years_max와 동일
    tiers: list[str] = []
    if is_housing_benefit_recipient:
        tiers.append("welfare_recipient")
    if is_married and (
        (marriage_years is not None and marriage_years <= cfg_years) or infants_count > 0
    ):
        tiers.append("newlywed")
    if is_single_parent and infants_count > 0:
        tiers.append("single_parent")
    if age >= 65:
        tiers.append("elderly")
    if 19 <= age <= 39 and not is_married:
        tiers.append("youth")
    return tiers


def _happy_max_residency(tier: str, infants_count: int, rules: dict[str, Any]) -> int | None:
    """계층별 최대 거주기간(자격이 아닌 참고 정보)."""
    table = rules["happy"]["max_residency_years"]
    if tier == "newlywed":
        return table["newlywed_with_child"] if infants_count > 0 else table["newlywed_no_child"]
    if tier == "single_parent":
        return table["newlywed_with_child"]
    return table.get(tier)


def judge_happy(
    *,
    age: int,
    is_married: bool | None,
    marriage_years: float | None,
    infants_count: int,
    is_single_parent: bool,
    is_housing_benefit_recipient: bool | None,
    income_ratio: float | None,
    household_size: int,
    is_dual_income: bool,
    real_estate_krw: int | None,
    car_value_krw: int | None,
    rules: dict[str, Any],
) -> dict[str, Any]:
    """행복주택: 계층 추론 → 계층별 소득·자산 대조. 첫 번째로 통과하는 계층을 채택한다."""
    cfg = rules["happy"]
    notes: list[str] = []
    tiers = infer_happy_tiers(
        age=age,
        is_married=is_married,
        marriage_years=marriage_years,
        infants_count=infants_count,
        is_single_parent=is_single_parent,
        is_housing_benefit_recipient=is_housing_benefit_recipient,
    )
    if not tiers:
        notes.append(
            "나이·혼인·수급 정보로는 해당 계층이 없습니다. 대학생·취업준비생·사회초년생·"
            "산업단지근로자 계층은 판정하지 않으므로, 해당된다면 공고문 자격을 확인하세요."
        )
        return {
            "eligible": False,
            "rank": None,
            "tier": None,
            "basis": "추론 가능한 행복주택 계층 없음",
            "max_residency_years": None,
            "notes": notes,
        }

    rejections: list[str] = []
    for tier in tiers:
        tier_cfg = cfg["tiers"][tier]
        cap = tier_cfg.get("income_pct_dual") if is_dual_income else tier_cfg.get("income_pct")
        cap = cap if cap is not None else tier_cfg.get("income_pct")
        income_ok = income_within_cap(income_ratio, cap, household_size, rules)
        if income_ok is False:
            rejections.append(f"{tier}: 소득 초과")
            continue
        asset_violations = check_assets("happy", tier, real_estate_krw, car_value_krw, rules)
        if asset_violations:
            rejections.extend(f"{tier}: {v['reason']}" for v in asset_violations)
            continue
        if income_ok is None and cap is not None:
            notes.append("소득표 밖(8인 이상 가구 등)이라 소득요건은 공고문으로 확인해야 합니다.")
        residency = _happy_max_residency(tier, infants_count, rules)
        return {
            "eligible": True,
            "rank": None,
            "tier": tier,
            "basis": f"행복주택 {tier} 계층 요건 충족",
            "max_residency_years": residency,
            "notes": notes,
        }

    notes.extend(rejections)
    return {
        "eligible": False,
        "rank": None,
        "tier": None,
        "basis": "해당 계층은 있으나 소득·자산 요건 미충족",
        "max_residency_years": None,
        "notes": notes,
    }


def judge_public(
    *,
    income_ratio: float | None,
    household_size: int,
    desired_size_sqm: float | None,
    account_months: int,
    payment_count: int,
    target_region: str,
    rules: dict[str, Any],
) -> dict[str, Any]:
    """공공임대(5·10년 분양전환): 통장 필수 → 60㎡ 이하 소득 100% → 우선/잔여공급."""
    cfg = rules["public"]
    notes: list[str] = []
    if account_months <= 0 and payment_count <= 0:
        return {
            "eligible": False,
            "rank": None,
            "basis": "공공임대는 청약통장(입주자저축) 가입이 필수입니다.",
            "notes": notes,
        }
    if desired_size_sqm is not None and desired_size_sqm > cfg["max_size_sqm"]:
        return {
            "eligible": False,
            "rank": None,
            "basis": f"전용 {cfg['max_size_sqm']}㎡ 초과는 공공임대(5·10년) 공급 대상이 아닙니다.",
            "notes": notes,
        }

    if desired_size_sqm is None:
        notes.append("희망 전용면적 미입력 — 60㎡ 이하(소득기준 적용) 기준으로 판정했습니다.")
    income_applies = desired_size_sqm is None or desired_size_sqm <= 60
    if income_applies:
        income_ok = income_within_cap(
            income_ratio, cfg["income_pct_upto_60sqm"], household_size, rules
        )
        if income_ok is False:
            return {
                "eligible": False,
                "rank": None,
                "basis": "60㎡ 이하 공공임대 소득 상한(100% + 1·2인 가산)을 초과합니다.",
                "notes": notes,
            }
        if income_ok is None:
            notes.append("소득표 밖(8인 이상 가구 등)이라 소득요건은 공고문으로 확인해야 합니다.")

    is_capital = any(token in target_region for token in _CAPITAL_TOKENS)
    req = cfg["rank1_account"]["capital" if is_capital else "non_capital"]
    if account_months >= req["account_months"] and payment_count >= req["payment_count"]:
        months = req["account_months"]
        payments = req["payment_count"]
        return {
            "eligible": True,
            "rank": 1,
            "basis": f"우선공급(통장 {months}개월·{payments}회 이상) 요건 충족",
            "notes": notes,
        }
    return {
        "eligible": True,
        "rank": None,
        "basis": "우선공급 통장 요건에는 미달하지만 잔여공급으로 신청할 수 있습니다.",
        "notes": notes,
    }


_RENTAL_TYPE_FIELD = "target_housing.rental_type"


def analyze_rental(doc: dict[str, Any], as_of: date | None = None) -> dict[str, Any]:
    """임대 프로필을 판정한다. 유형 미지정이면 4유형 전부 스크리닝한다.

    분양 engine.analyze와 같은 needs_more_info/provisional 철학을 따르되,
    rental_type 누락만으로는 판정을 막지 않는다(전 유형 스크리닝이 유형 추천 역할).
    """
    as_of = as_of or date.today()
    core_missing, full_missing, optional_missing = missing_fields(doc)
    blocking_core = [item for item in core_missing if item["field"] != _RENTAL_TYPE_FIELD]
    if blocking_core:
        return {
            "status": "needs_more_info",
            "missing_required_fields": core_missing,
            "missing_recommended_fields": full_missing,
            "missing_optional_fields": optional_missing,
            "guidance": "잠정 판정에 필요한 core 항목을 채운 뒤 다시 분석하세요. "
            "update_my_profile로 부분 업데이트할 수 있습니다.",
        }
    is_provisional = bool(full_missing) or bool(core_missing)

    profile = ProfileDocument.model_validate(doc)
    user = profile.user_profile
    account = profile.subscription_account
    target = profile.target_housing
    rules = load_rental_rules()

    action_items: list[str] = []
    notes: list[str] = []

    derived_age = _age_from_birth_date(user.birth_date, as_of)
    age = derived_age if derived_age is not None else (user.age or 0)
    dependents = user.dependents_count or 0
    household_size = dependents + 1
    if user.dependents_count is None:
        action_items.append("부양가족 수를 입력하면 가구원수 기준 소득 상한이 정확해집니다.")
    income = user.income_and_assets.monthly_income_krw or 0
    income_ratio = rental_income_ratio_pct(income, household_size, rules)
    marriage_years = (
        _years_since(user.marriage.marriage_date, as_of) if user.marriage.marriage_date else None
    )
    real_estate = user.income_and_assets.total_real_estate_krw
    car_value = user.income_and_assets.car_value_krw
    if real_estate is None:
        action_items.append("세대 부동산 자산을 입력하면 총자산 상한을 판정합니다.")
    if car_value is None:
        action_items.append("자동차 가액을 입력하면 자동차 상한을 판정합니다.")

    # 공통 차단필터: 무주택 세대구성원. 임대는 제53조(60세 이상 직계존속) 예외가 공공임대에
    # 적용되지 않는 등 분양보다 엄격해, 세대 보유 주택 수로 단순 판정하고 예외는 안내만 한다.
    owned = user.owned_house_count or 0
    is_homeless_household = owned == 0
    disqualifications: list[dict[str, str]] = []
    if not is_homeless_household:
        disqualifications.append(
            {
                "filter": "homeless_household",
                "reason": f"세대 보유 주택 {owned}채 — 임대주택은 무주택 세대구성원만 "
                "신청할 수 있습니다.",
            }
        )

    types_to_judge = (
        [target.rental_type.value]
        if target.rental_type is not None
        else ["permanent", "national", "happy", "public"]
    )
    if target.rental_type is None:
        notes.append("임대 유형 미지정 — 4유형 전부를 스크리닝해 신청 가능 유형을 추렸습니다.")

    judgments: dict[str, dict[str, Any]] = {}
    for rental_type in types_to_judge:
        judgments[rental_type] = _judge_one(
            rental_type,
            is_homeless_household=is_homeless_household,
            age=age,
            income_ratio=income_ratio,
            household_size=household_size,
            user=user,
            account=account,
            target=target,
            marriage_years=marriage_years,
            real_estate=real_estate,
            car_value=car_value,
            rules=rules,
        )

    eligible_types = [t for t, j in judgments.items() if j["eligible"]]

    notes.append(
        "이 판정은 마이홈포털 2026년도 일반 고시 기준의 잠정 판정입니다. 단지별 기준은 "
        "공고문이 최종이므로 search_lease_notices로 공고를 찾고 extract_lease_notice_text로 "
        "원문의 소득·자산·순위 기준을 대조하세요."
    )

    return {
        "status": "ok",
        "confidence": "provisional" if is_provisional else "complete",
        "track": "rental",
        "rental_type": target.rental_type.value if target.rental_type else None,
        "headline": _headline(judgments, eligible_types, is_provisional),
        "blocking": {
            "is_homeless_household": is_homeless_household,
            "disqualifications": disqualifications,
        },
        "judgments": judgments,
        "eligible_types": eligible_types,
        "action_items": action_items,
        "verification_notes": notes,
    }


def _judge_one(
    rental_type: str,
    *,
    is_homeless_household: bool,
    age: int,
    income_ratio: float | None,
    household_size: int,
    user: Any,
    account: Any,
    target: Any,
    marriage_years: float | None,
    real_estate: int | None,
    car_value: int | None,
    rules: dict[str, Any],
) -> dict[str, Any]:
    """유형 하나를 판정한다: 무주택 → (행복 외) 자산 → 유형별 판정."""
    if not is_homeless_household:
        return {
            "eligible": False,
            "rank": None,
            "basis": "무주택 세대구성원 요건 미충족",
            "notes": [],
        }
    if rental_type != "happy":  # 행복주택 자산은 계층별이라 judge_happy 안에서 검사
        violations = check_assets(rental_type, None, real_estate, car_value, rules)
        if violations:
            return {
                "eligible": False,
                "rank": None,
                "basis": "; ".join(v["reason"] for v in violations),
                "notes": [],
            }
    if rental_type == "permanent":
        return judge_permanent(
            age=age,
            income_ratio=income_ratio,
            household_size=household_size,
            is_basic_living_recipient=user.welfare.is_basic_living_recipient,
            is_national_merit=user.welfare.is_national_merit,
            is_near_poverty=user.welfare.is_near_poverty,
            is_single_parent=user.is_single_parent,
            rules=rules,
        )
    if rental_type == "national":
        return judge_national(
            income_ratio=income_ratio,
            household_size=household_size,
            desired_size_sqm=target.desired_size_sqm,
            account_months=account.duration_months or 0,
            payment_count=account.payment_count or 0,
            age=age,
            dependents_count=user.dependents_count or 0,
            residence_years=user.residence_years_in_region or 0,
            children_count=user.children_count or 0,
            rules=rules,
        )
    if rental_type == "happy":
        return judge_happy(
            age=age,
            is_married=user.marriage.is_married,
            marriage_years=marriage_years,
            infants_count=user.infants_count or 0,
            is_single_parent=user.is_single_parent,
            is_housing_benefit_recipient=user.welfare.is_housing_benefit_recipient,
            income_ratio=income_ratio,
            household_size=household_size,
            is_dual_income=user.income_and_assets.is_dual_income,
            real_estate_krw=real_estate,
            car_value_krw=car_value,
            rules=rules,
        )
    return judge_public(
        income_ratio=income_ratio,
        household_size=household_size,
        desired_size_sqm=target.desired_size_sqm,
        account_months=account.duration_months or 0,
        payment_count=account.payment_count or 0,
        target_region=target.target_region or user.residence_area or "",
        rules=rules,
    )


_TYPE_LABEL = {
    "permanent": "영구임대",
    "national": "국민임대",
    "happy": "행복주택",
    "public": "공공임대",
}


def _headline(
    judgments: dict[str, dict[str, Any]], eligible_types: list[str], is_provisional: bool
) -> str:
    """한 줄 결론. 분양 엔진의 headline 철학(먼저 결론, 단정 금지)을 따른다."""
    suffix = " (잠정 — 공고문 확인 필요)" if is_provisional else " (공고문 확인 필요)"
    if not eligible_types:
        return "현재 입력 기준으로 신청 가능한 임대 유형이 없습니다" + suffix
    parts = []
    for rental_type in eligible_types:
        judgment = judgments[rental_type]
        label = _TYPE_LABEL[rental_type]
        if judgment.get("rank"):
            parts.append(f"{label} {judgment['rank']}순위")
        elif judgment.get("tier"):
            parts.append(f"{label}({judgment['tier']} 계층)")
        else:
            parts.append(label)
    return ", ".join(parts) + " 신청 자격이 있습니다" + suffix

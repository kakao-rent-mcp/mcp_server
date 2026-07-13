# 임대 자격 자동판정 (analyze_my_rental) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 임대주택 4유형(영구·국민·행복·공공)의 자격·순위를 `config/rental_rules.yaml` 기준표로 자동 판정하는 `analyze_my_rental` MCP 도구를 추가한다.

**Architecture:** 분양 `engine.py`와 분리된 `rental_engine.py`(순수 계산, 네트워크 없음)가 유형별 판정 함수를 오케스트레이션한다. 스펙은 `docs/rental-policy-spec.md`, 기준표는 `src/slug_mcp/config/rental_rules.yaml`(이미 커밋됨). 판정은 항상 '일반 고시 기준 잠정판정'이며 공고문 대조 안내를 붙인다.

**Tech Stack:** Python 3.12+, pydantic v2, FastMCP, pytest, ruff. 의존성 추가 없음.

## Global Constraints

- 테스트 실행: `uv run pytest` (uv가 PATH에 없으면 `.venv/bin/pytest`). ruff도 동일.
- **커밋 전 반드시 `uv run ruff format .` 실행** — CI가 `ruff format --check`로 막는다 (2026-07-12 CI 실패 사례 있음).
- 커밋 메시지는 기존 관례를 따른다: `(feat) 한국어 요약` + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- 금액은 전부 원(KRW). YAML의 `asset_limits_10k_won`만 만원 단위(코드에서 ×10_000).
- 판정 결과 문구에서 단정 금지: "잠정", "일반 고시 기준", "공고문 확인" 표현 유지 (스펙의 참고 기준선 철학).
- 새 docs 파일은 `.gitignore`의 `docs/` 규칙 때문에 `git add -f` 필요. 코드·테스트는 무관.

---

### Task 1: 임대 룰 로더 (`load_rental_rules`)

**Files:**
- Modify: `src/slug_mcp/rules.py` (현재 20줄 — 로더 함수 하나 추가)
- Test: `tests/test_rental_engine.py` (새 파일)

**Interfaces:**
- Produces: `rules.load_rental_rules() -> dict[str, Any]` — `config/rental_rules.yaml` 파싱 결과. 이후 모든 태스크가 이 dict를 `rules` 인자로 받는다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_rental_engine.py` 생성:

```python
"""임대 자격 판정 엔진(rental_engine) 테스트.

기준표는 config/rental_rules.yaml (마이홈포털 2026년도 적용기준,
docs/rental-policy-spec.md 참조). 판정은 일반 고시 기준 잠정판정이다.
"""

from __future__ import annotations

from slug_mcp.rules import load_rental_rules


def test_rental_rules_load_and_have_expected_keys():
    rules = load_rental_rules()
    # 소득표는 임대용 개별 행(1·2·3인) 체계 — 분양표("3인 이하" 통합)와 다르다.
    assert rules["rental_income_100pct_krw"]["1"] == 3813363
    assert rules["household_income_bonus_pct"]["1"] == 20
    assert rules["asset_limits_10k_won"]["national"]["total_asset"] == 34500
    for rental_type in ("permanent", "national", "happy", "public"):
        assert rental_type in rules
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_rental_engine.py -v`
Expected: FAIL — `ImportError: cannot import name 'load_rental_rules'`

- [ ] **Step 3: 최소 구현**

`src/slug_mcp/rules.py`의 `load_rules` 아래에 추가:

```python
@lru_cache(maxsize=1)
def load_rental_rules() -> dict[str, Any]:
    """config/rental_rules.yaml (임대 4유형 기준표) 로더. 근거는 docs/rental-policy-spec.md."""
    path = resources.files("slug_mcp.config").joinpath("rental_rules.yaml")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)
```

docstring 첫 줄(모듈)도 갱신: `"""config/*.yaml 로더 (분양 eligibility_rules / 임대 rental_rules)."""`

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_rental_engine.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
uv run ruff format . && git add src/slug_mcp/rules.py tests/test_rental_engine.py
git commit -m "(feat) 임대 기준표(rental_rules.yaml) 로더 추가"
```

---

### Task 2: 임대용 소득 환산·상한 헬퍼

**Files:**
- Create: `src/slug_mcp/rental_engine.py`
- Test: `tests/test_rental_engine.py`

**Interfaces:**
- Consumes: `load_rental_rules()` (Task 1)
- Produces:
  - `rental_income_ratio_pct(monthly_income_krw: int, household_size: int, rules: dict) -> float | None` — 임대 소득표 대비 %. 8인 이상(표 밖)이면 None.
  - `income_within_cap(income_ratio: float | None, cap_pct: float | None, household_size: int, rules: dict) -> bool | None` — 가구 가산(+20/+10%p) 반영 상한 검사. None = 판정 불가(소득표 밖 또는 상한 없음).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_rental_engine.py`에 추가:

```python
from slug_mcp import rental_engine


def test_rental_income_ratio_uses_per_size_rows_not_sale_table():
    rules = load_rental_rules()
    # 1인 가구 기본값 3,813,363원 — 분양표의 "3인 이하" 통합값(7,533,763)이 아니어야 한다.
    ratio = rental_engine.rental_income_ratio_pct(3_813_363, 1, rules)
    assert ratio is not None and round(ratio) == 100
    # 8인 이상은 고시 미확인 — None(판정 불가).
    assert rental_engine.rental_income_ratio_pct(5_000_000, 8, rules) is None


def test_income_within_cap_applies_small_household_bonus():
    rules = load_rental_rules()
    # 1인 가구 70% 기준의 실효 상한은 90% (마이홈 공표값 3,432,027원 ≈ 90%).
    # 공표값은 반올림돼 90.000008%가 되므로 경계값 대신 여유 있는 소득으로 검사한다.
    ratio = rental_engine.rental_income_ratio_pct(3_400_000, 1, rules)  # 약 89.2%
    assert rental_engine.income_within_cap(ratio, 70, 1, rules) is True
    assert rental_engine.income_within_cap(ratio, 50, 1, rules) is False  # 50%+20%p=70% 초과
    # 상한 없음(행복주택 수급자 계층 등) / 소득표 밖 → 판정 불가 None.
    assert rental_engine.income_within_cap(ratio, None, 1, rules) is None
    assert rental_engine.income_within_cap(None, 70, 1, rules) is None
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_rental_engine.py -v`
Expected: FAIL — `ImportError: cannot import name 'rental_engine'`

- [ ] **Step 3: 최소 구현**

`src/slug_mcp/rental_engine.py` 생성:

```python
"""임대주택(영구·국민·행복·공공) 자격 판정 엔진 — rental_rules.yaml 기반 순수 계산.

분양 engine.py와 분리한 이유: 분양은 경쟁 점수 계산(가점·납입총액), 임대는
기준표 대조 + 순위 결정으로 판정 구조가 다르다. 기준값은 일반 고시 기준이므로
판정 결과에는 항상 공고문 대조 안내를 붙인다 (docs/rental-policy-spec.md).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .models import ProfileDocument, missing_fields
from .rules import load_rental_rules


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
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_rental_engine.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
uv run ruff format . && git add src/slug_mcp/rental_engine.py tests/test_rental_engine.py
git commit -m "(feat) 임대용 소득 환산·가구 가산 상한 헬퍼 (rental_engine 시작)"
```

---

### Task 3: 자산 차단필터 (`check_assets`)

**Files:**
- Modify: `src/slug_mcp/rental_engine.py`
- Test: `tests/test_rental_engine.py`

**Interfaces:**
- Produces: `check_assets(rental_type: str, happy_tier: str | None, real_estate_krw: int | None, car_value_krw: int | None, rules: dict) -> list[dict[str, str]]` — 위반 목록 `[{"filter": "asset"|"vehicle", "reason": str}]`. 빈 리스트 = 통과. `rental_type`은 `"permanent"|"national"|"happy"|"public"`, happy일 때만 `happy_tier` 사용.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_check_assets_blocks_over_limit_and_reports_reason():
    rules = load_rental_rules()
    # 국민임대 총자산 상한 3억 4,500만원: 부동산만으로 초과하면 확정 탈락.
    violations = rental_engine.check_assets("national", None, 400_000_000, None, rules)
    assert [v["filter"] for v in violations] == ["asset"]
    # 자동차 4,542만원 초과.
    violations = rental_engine.check_assets("national", None, None, 50_000_000, rules)
    assert [v["filter"] for v in violations] == ["vehicle"]
    # 공공임대는 총자산이 아니라 부동산 상한(2억 1,550만원)을 쓴다.
    assert rental_engine.check_assets("public", None, 300_000_000, None, rules)
    assert not rental_engine.check_assets("public", None, 200_000_000, 40_000_000, rules)


def test_check_assets_happy_uses_tier_limits():
    rules = load_rental_rules()
    # 청년 계층 총자산 2억 5,100만원 — 신혼(3억 4,500만원)보다 낮다.
    assert rental_engine.check_assets("happy", "youth", 300_000_000, None, rules)
    assert not rental_engine.check_assets("happy", "newlywed", 300_000_000, None, rules)
    # 대학생 계층은 자동차 소유 불가(vehicle: 0).
    assert rental_engine.check_assets("happy", "college_student", None, 1_000_000, rules)
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_rental_engine.py -v`
Expected: FAIL — `AttributeError: ... no attribute 'check_assets'`

- [ ] **Step 3: 최소 구현**

`rental_engine.py`에 추가:

```python
# 행복주택 계층 → 자산 상한 키 (한부모는 신혼부부와 동일 상한을 쓴다).
_HAPPY_ASSET_KEY = {
    "youth": "youth",
    "newlywed": "newlywed",
    "single_parent": "newlywed",
    "elderly": "elderly",
    "welfare_recipient": "welfare_recipient",
    "college_student": "college_student",
}


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
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_rental_engine.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
uv run ruff format . && git add -u && git commit -m "(feat) 임대 유형·계층별 자산 차단필터"
```

---

### Task 4: 영구임대 판정 (`judge_permanent`)

**Files:**
- Modify: `src/slug_mcp/rental_engine.py`
- Test: `tests/test_rental_engine.py`

**Interfaces:**
- Consumes: `income_within_cap` (Task 2)
- Produces: `judge_permanent(*, age: int, income_ratio: float | None, household_size: int, is_basic_living_recipient: bool | None, is_national_merit: bool | None, is_near_poverty: bool | None, is_single_parent: bool, rules: dict) -> dict` — 반환 키: `eligible: bool`, `rank: int | None`, `basis: str`, `notes: list[str]`. (모든 judge_* 함수가 이 4키를 공통으로 돌려준다.)

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def _judge_permanent(**overrides):
    kwargs = dict(
        age=40,
        income_ratio=52.0,
        household_size=1,
        is_basic_living_recipient=False,
        is_national_merit=False,
        is_near_poverty=False,
        is_single_parent=False,
        rules=load_rental_rules(),
    )
    kwargs.update(overrides)
    return rental_engine.judge_permanent(**kwargs)


def test_permanent_recipient_is_rank1():
    result = _judge_permanent(is_basic_living_recipient=True)
    assert (result["eligible"], result["rank"]) == (True, 1)
    assert "수급자" in result["basis"]


def test_permanent_income_under_50pct_is_rank2_with_bonus_note():
    # 1인 가구 소득 52% — 50% 초과지만 1인 가산(+20%p)으로 2순위 충족.
    result = _judge_permanent()
    assert (result["eligible"], result["rank"]) == (True, 2)
    assert any("공고문" in note for note in result["notes"])  # 가산 요검증 안내


def test_permanent_over_income_without_priority_is_ineligible():
    result = _judge_permanent(income_ratio=95.0)
    assert (result["eligible"], result["rank"]) == (False, None)
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_rental_engine.py -v`
Expected: FAIL — `AttributeError: ... no attribute 'judge_permanent'`

- [ ] **Step 3: 최소 구현**

```python
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
        return {"eligible": True, "rank": 1, "basis": "지원대상 한부모가족", "notes": notes}
    merit_ok = income_within_cap(
        income_ratio, cfg["rank1"]["national_merit_income_pct"], household_size, rules
    )
    if is_national_merit and merit_ok:
        return {"eligible": True, "rank": 1, "basis": "국가유공자(소득 70% 이하)", "notes": notes}
    if is_near_poverty and age >= 65:
        return {"eligible": True, "rank": 1, "basis": "만 65세 이상 수급권자·차상위", "notes": notes}
    if income_within_cap(income_ratio, cfg["rank2"]["income_pct"], household_size, rules):
        notes.append(
            "영구임대 2순위의 1·2인 가구 소득 가산은 고시 표기가 엇갈려 공통 가산"
            "(+20/+10%p)을 적용했습니다 — 공고문 기준을 확인하세요."
        )
        return {"eligible": True, "rank": 2, "basis": "소득 50% 이하(가산 반영)", "notes": notes}
    return {
        "eligible": False,
        "rank": None,
        "basis": "수급·한부모·유공자 자격이 없고 소득이 2순위 상한을 초과합니다.",
        "notes": notes,
    }
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_rental_engine.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
uv run ruff format . && git add -u && git commit -m "(feat) 영구임대 순위 판정 (수급자 1순위·소득 50% 2순위)"
```

---

### Task 5: 국민임대 판정 (`judge_national` + 배점)

**Files:**
- Modify: `src/slug_mcp/rental_engine.py`
- Test: `tests/test_rental_engine.py`

**Interfaces:**
- Produces:
  - `national_tiebreak_score(*, age: int, dependents_count: int, residence_years: int, children_count: int, payment_count: int, rules: dict) -> dict[str, Any]` — 키: 항목별 점수(`age`, `dependents`, `residence_years`, `minor_children`, `payment_count`, `elderly_care`)와 `total: int`, `notes: list[str]`.
  - `judge_national(*, income_ratio: float | None, household_size: int, desired_size_sqm: float | None, account_months: int, payment_count: int, age: int, dependents_count: int, residence_years: int, children_count: int, rules: dict) -> dict` — 공통 4키 + `tiebreak: dict`(위 배점 결과).

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_national_tiebreak_score_brackets():
    rules = load_rental_rules()
    score = rental_engine.national_tiebreak_score(
        age=52, dependents_count=2, residence_years=6, children_count=1,
        payment_count=70, rules=rules,
    )
    # 나이 50+→3, 부양 2인→2, 거주 5년+→3, 미성년 1명→0(표는 2명부터), 납입 60회+→3.
    assert (score["age"], score["dependents"], score["residence_years"]) == (3, 2, 3)
    assert (score["minor_children"], score["payment_count"]) == (0, 3)
    # 고령부양 항목은 프로필 미수집 — 0점 + 안내.
    assert score["elderly_care"] == 0
    assert score["total"] == 11
    assert any("고령" in note for note in score["notes"])


def _judge_national(**overrides):
    kwargs = dict(
        income_ratio=60.0, household_size=3, desired_size_sqm=59.0,
        account_months=30, payment_count=30, age=40, dependents_count=2,
        residence_years=3, children_count=1, rules=load_rental_rules(),
    )
    kwargs.update(overrides)
    return rental_engine.judge_national(**kwargs)


def test_national_income_over_cap_is_ineligible():
    # 3인 가구는 가산 없음 — 70% 초과 시 탈락.
    result = _judge_national(income_ratio=73.0)
    assert result["eligible"] is False


def test_national_50sqm_or_more_ranks_by_account():
    assert _judge_national(account_months=30, payment_count=30)["rank"] == 1  # 24개월·24회 이상
    assert _judge_national(account_months=12, payment_count=12)["rank"] == 2  # 6개월·6회 이상
    assert _judge_national(account_months=0, payment_count=0)["rank"] == 3


def test_national_under_50sqm_rank_needs_sigungu_and_says_so():
    result = _judge_national(desired_size_sqm=40.0, income_ratio=45.0)
    assert result["eligible"] is True
    assert result["rank"] is None  # 거주 시·군·구를 수집하지 않아 당해/연접 판정 불가
    assert any("거주" in note for note in result["notes"])
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_rental_engine.py -v`
Expected: FAIL — `AttributeError: ... no attribute 'national_tiebreak_score'`

- [ ] **Step 3: 최소 구현**

```python
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
        return {"eligible": True, "rank": None, "basis": "소득요건 충족(50㎡ 미만, 거주지 순위)",
                "notes": notes, "tiebreak": tiebreak}

    if desired_size_sqm is None:
        notes.append("희망 전용면적 미입력 — 50㎡ 이상(통장 순위) 기준으로 판정했습니다.")
    rank_cfg = cfg["rank_50sqm_or_more"]
    if account_months >= rank_cfg["rank1"]["account_months"] and payment_count >= rank_cfg["rank1"]["payment_count"]:
        rank = 1
    elif account_months >= rank_cfg["rank2"]["account_months"] and payment_count >= rank_cfg["rank2"]["payment_count"]:
        rank = 2
    else:
        rank = 3
    return {"eligible": True, "rank": rank, "basis": f"소득요건 충족, 통장 기준 {rank}순위",
            "notes": notes, "tiebreak": tiebreak}
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_rental_engine.py -v`
Expected: PASS (12 passed)

- [ ] **Step 5: Commit**

```bash
uv run ruff format . && git add -u && git commit -m "(feat) 국민임대 판정 — 소득컷·면적별 순위·동순위 배점표"
```

---

### Task 6: 행복주택 판정 (계층 추론 + `judge_happy`)

**Files:**
- Modify: `src/slug_mcp/rental_engine.py`
- Test: `tests/test_rental_engine.py`

**Interfaces:**
- Produces:
  - `infer_happy_tiers(*, age: int, is_married: bool | None, marriage_years: float | None, infants_count: int, is_single_parent: bool, is_housing_benefit_recipient: bool | None) -> list[str]` — 해당 가능한 계층 키 목록(우선순위순). 키: `"welfare_recipient" | "newlywed" | "single_parent" | "elderly" | "youth"`.
  - `judge_happy(*, age, is_married, marriage_years, infants_count, is_single_parent, is_housing_benefit_recipient, income_ratio, household_size, is_dual_income, real_estate_krw, car_value_krw, rules) -> dict` — 공통 4키 + `tier: str | None`, `max_residency_years: int | None`. (타입은 infer와 동일 + `income_ratio: float | None`, `household_size: int`, `is_dual_income: bool`, `real_estate_krw: int | None`, `car_value_krw: int | None`, `rules: dict`.)
- Consumes: `check_assets` (Task 3) — 행복주택 자산은 계층별이라 이 함수 안에서 검사한다.
- 주의: `is_housing_benefit_recipient` 프로필 필드는 Task 8에서 추가된다. 이 태스크의 판정 함수는 인자로만 받으므로 선행 가능.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_infer_happy_tiers_priority_and_conditions():
    # 34세 기혼 5년차: 신혼부부. 미혼이면 청년. 66세면 고령자.
    assert rental_engine.infer_happy_tiers(
        age=34, is_married=True, marriage_years=5.0, infants_count=0,
        is_single_parent=False, is_housing_benefit_recipient=None,
    ) == ["newlywed"]
    assert rental_engine.infer_happy_tiers(
        age=34, is_married=False, marriage_years=None, infants_count=0,
        is_single_parent=False, is_housing_benefit_recipient=None,
    ) == ["youth"]
    tiers = rental_engine.infer_happy_tiers(
        age=66, is_married=False, marriage_years=None, infants_count=0,
        is_single_parent=False, is_housing_benefit_recipient=True,
    )
    assert tiers[0] == "welfare_recipient" and "elderly" in tiers
    # 혼인 10년차라도 6세 이하 자녀가 있으면 신혼부부 계층(OR 조건).
    assert "newlywed" in rental_engine.infer_happy_tiers(
        age=42, is_married=True, marriage_years=10.0, infants_count=1,
        is_single_parent=False, is_housing_benefit_recipient=None,
    )


def _judge_happy(**overrides):
    kwargs = dict(
        age=30, is_married=False, marriage_years=None, infants_count=0,
        is_single_parent=False, is_housing_benefit_recipient=None,
        income_ratio=90.0, household_size=1, is_dual_income=False,
        real_estate_krw=100_000_000, car_value_krw=None, rules=load_rental_rules(),
    )
    kwargs.update(overrides)
    return rental_engine.judge_happy(**kwargs)


def test_happy_youth_passes_with_household_bonus():
    # 1인 가구 청년: 소득 상한 100% + 20%p 가산 = 120%. 90%는 통과, 125%는 탈락.
    result = _judge_happy()
    assert (result["eligible"], result["tier"]) == (True, "youth")
    assert result["max_residency_years"] == 10
    assert _judge_happy(income_ratio=125.0)["eligible"] is False


def test_happy_no_matching_tier_is_ineligible_with_guidance():
    # 45세 미혼 무자녀: 어떤 계층도 아님. 대학생·산단근로자 가능성 안내.
    result = _judge_happy(age=45)
    assert result["eligible"] is False and result["tier"] is None
    assert any("대학생" in note or "산업단지" in note for note in result["notes"])


def test_happy_asset_check_uses_tier_limits():
    # 청년 총자산 상한 2억 5,100만원 초과 → 탈락.
    result = _judge_happy(real_estate_krw=260_000_000)
    assert result["eligible"] is False
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_rental_engine.py -v`
Expected: FAIL — `AttributeError: ... no attribute 'infer_happy_tiers'`

- [ ] **Step 3: 최소 구현**

```python
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
    if is_married and ((marriage_years is not None and marriage_years <= cfg_years) or infants_count > 0):
        tiers.append("newlywed")
    if is_single_parent and infants_count > 0:
        tiers.append("single_parent")
    if age >= 65:
        tiers.append("elderly")
    if 19 <= age <= 39 and not is_married:
        tiers.append("youth")
    return tiers


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
        return {"eligible": False, "rank": None, "tier": None,
                "basis": "추론 가능한 행복주택 계층 없음", "max_residency_years": None, "notes": notes}

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
        return {"eligible": True, "rank": None, "tier": tier,
                "basis": f"행복주택 {tier} 계층 요건 충족",
                "max_residency_years": residency, "notes": notes}

    notes.extend(rejections)
    return {"eligible": False, "rank": None, "tier": None,
            "basis": "해당 계층은 있으나 소득·자산 요건 미충족",
            "max_residency_years": None, "notes": notes}


def _happy_max_residency(tier: str, infants_count: int, rules: dict[str, Any]) -> int | None:
    """계층별 최대 거주기간(자격이 아닌 참고 정보)."""
    table = rules["happy"]["max_residency_years"]
    if tier == "newlywed":
        return table["newlywed_with_child"] if infants_count > 0 else table["newlywed_no_child"]
    if tier == "single_parent":
        return table["newlywed_with_child"]
    return table.get(tier)
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_rental_engine.py -v`
Expected: PASS (17 passed)

- [ ] **Step 5: Commit**

```bash
uv run ruff format . && git add -u && git commit -m "(feat) 행복주택 판정 — 계층 추론과 계층별 소득·자산 대조"
```

---

### Task 7: 공공임대 판정 (`judge_public`)

**Files:**
- Modify: `src/slug_mcp/rental_engine.py`
- Test: `tests/test_rental_engine.py`

**Interfaces:**
- Produces: `judge_public(*, income_ratio: float | None, household_size: int, desired_size_sqm: float | None, account_months: int, payment_count: int, target_region: str, rules: dict) -> dict` — 공통 4키(`eligible`, `rank`, `basis`, `notes`). `rank=1`은 우선공급, `rank=None`+eligible은 잔여공급.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def _judge_public(**overrides):
    kwargs = dict(
        income_ratio=90.0, household_size=4, desired_size_sqm=59.0,
        account_months=13, payment_count=13, target_region="경기 하남",
        rules=load_rental_rules(),
    )
    kwargs.update(overrides)
    return rental_engine.judge_public(**kwargs)


def test_public_needs_account():
    result = _judge_public(account_months=0, payment_count=0)
    assert result["eligible"] is False
    assert "통장" in result["basis"]


def test_public_rank1_differs_by_capital_region():
    # 수도권(경기)은 12개월·12회, 비수도권(부산)은 6개월·6회가 우선공급 기준.
    assert _judge_public()["rank"] == 1
    assert _judge_public(account_months=7, payment_count=7)["rank"] is None  # 수도권 미달 → 잔여
    assert _judge_public(account_months=7, payment_count=7, target_region="부산")["rank"] == 1


def test_public_income_only_applies_upto_60sqm():
    # 60㎡ 이하만 소득 100% 적용 — 초과 평형은 소득 무관.
    assert _judge_public(income_ratio=130.0)["eligible"] is False
    assert _judge_public(income_ratio=130.0, desired_size_sqm=84.0)["eligible"] is True
    # 85㎡ 초과는 공공임대(5·10년) 대상이 아니다.
    assert _judge_public(desired_size_sqm=101.0)["eligible"] is False
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_rental_engine.py -v`
Expected: FAIL — `AttributeError: ... no attribute 'judge_public'`

- [ ] **Step 3: 최소 구현**

```python
_CAPITAL_TOKENS = ("서울", "경기", "인천")  # engine._CAPITAL_SIDO_TOKENS와 동일 기준


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
        return {"eligible": False, "rank": None,
                "basis": "공공임대는 청약통장(입주자저축) 가입이 필수입니다.", "notes": notes}
    if desired_size_sqm is not None and desired_size_sqm > cfg["max_size_sqm"]:
        return {"eligible": False, "rank": None,
                "basis": f"전용 {cfg['max_size_sqm']}㎡ 초과는 공공임대(5·10년) 공급 대상이 "
                "아닙니다.", "notes": notes}

    if desired_size_sqm is None:
        notes.append("희망 전용면적 미입력 — 60㎡ 이하(소득기준 적용) 기준으로 판정했습니다.")
    income_applies = desired_size_sqm is None or desired_size_sqm <= 60
    if income_applies:
        income_ok = income_within_cap(
            income_ratio, cfg["income_pct_upto_60sqm"], household_size, rules
        )
        if income_ok is False:
            return {"eligible": False, "rank": None,
                    "basis": "60㎡ 이하 공공임대 소득 상한(100% + 1·2인 가산)을 초과합니다.",
                    "notes": notes}
        if income_ok is None:
            notes.append("소득표 밖(8인 이상 가구 등)이라 소득요건은 공고문으로 확인해야 합니다.")

    is_capital = any(token in target_region for token in _CAPITAL_TOKENS)
    req = cfg["rank1_account"]["capital" if is_capital else "non_capital"]
    if account_months >= req["account_months"] and payment_count >= req["payment_count"]:
        return {"eligible": True, "rank": 1,
                "basis": f"우선공급(통장 {req['account_months']}개월·{req['payment_count']}회 "
                "이상) 요건 충족", "notes": notes}
    return {"eligible": True, "rank": None,
            "basis": "우선공급 통장 요건에는 미달하지만 잔여공급으로 신청할 수 있습니다.",
            "notes": notes}
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_rental_engine.py -v`
Expected: PASS (20 passed)

- [ ] **Step 5: Commit**

```bash
uv run ruff format . && git add -u && git commit -m "(feat) 공공임대 판정 — 통장 필수·60㎡ 소득기준·우선/잔여공급"
```

---

### Task 8: 모델 확장 — 주거급여 수급 필드

**Files:**
- Modify: `src/slug_mcp/models.py` (WelfareStatus 클래스, RENTAL_OPTIONAL_FIELD_QUESTIONS)
- Test: `tests/test_rental_profile.py`

**Interfaces:**
- Produces: `WelfareStatus.is_housing_benefit_recipient: bool | None` — Task 9의 orchestrator가 읽는다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_rental_profile.py`에 추가 (기존 import 재사용):

```python
def test_welfare_has_housing_benefit_field_and_rental_optional_question():
    from slug_mcp.models import RENTAL_OPTIONAL_FIELD_QUESTIONS

    assert WelfareStatus(is_housing_benefit_recipient=True).is_housing_benefit_recipient is True
    assert (
        "user_profile.welfare.is_housing_benefit_recipient" in RENTAL_OPTIONAL_FIELD_QUESTIONS
    )
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_rental_profile.py -v`
Expected: FAIL — `ValidationError` 또는 `unexpected keyword argument`

- [ ] **Step 3: 최소 구현**

`models.py` `WelfareStatus`의 `is_near_poverty` 아래에 추가:

```python
    is_housing_benefit_recipient: bool | None = Field(
        default=None, description="주거급여 수급자 여부 (행복주택 주거급여수급자 계층 판단)"
    )
```

`RENTAL_OPTIONAL_FIELD_QUESTIONS`의 `is_near_poverty` 항목 아래에 추가:

```python
    "user_profile.welfare.is_housing_benefit_recipient": (
        "주거급여를 받고 계신가요? (행복주택 주거급여수급자 계층 — 소득기준 없이 신청 가능)"
    ),
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_rental_profile.py -v`
Expected: PASS (기존 테스트 포함 전부)

- [ ] **Step 5: Commit**

```bash
uv run ruff format . && git add -u && git commit -m "(feat) 주거급여 수급 필드 추가 — 행복주택 계층 판단용"
```

---

### Task 9: 오케스트레이터 (`analyze_rental`)

**Files:**
- Modify: `src/slug_mcp/rental_engine.py`
- Test: `tests/test_rental_engine.py`

**Interfaces:**
- Consumes: Task 2~8의 모든 함수, `models.missing_fields`, `engine._age_from_birth_date`, `engine._years_since` (같은 패키지 내 재사용 — 나이·혼인연차 파생 로직 중복 방지).
- Produces: `analyze_rental(doc: dict[str, Any], as_of: date | None = None) -> dict[str, Any]` — Task 10의 도구가 그대로 반환. 반환 스키마:
  - `status: "needs_more_info" | "ok"` (+ needs_more_info면 engine.analyze와 같은 missing_* 3키와 guidance)
  - ok일 때: `confidence: "provisional" | "complete"`, `track: "rental"`, `rental_type: str | None`, `headline: str`, `blocking: {"is_homeless_household": bool, "disqualifications": list}`, `judgments: dict[str, dict]` (유형 지정 시 그 유형 1개, 미지정 시 4유형 전부), `eligible_types: list[str]`, `action_items: list[str]`, `verification_notes: list[str]`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def _rental_doc(**overrides):
    """core가 전부 찬 영구임대 기본 문서. overrides는 최상위 키 단위로 deep-merge한다."""
    doc = {
        "target_housing": {"track": "rental", "rental_type": "permanent",
                           "target_region": "성남시"},
        "user_profile": {
            "age": 40, "residence_area": "경기", "owned_house_count": 0,
            "income_and_assets": {"monthly_income_krw": 2_000_000},
            "welfare": {"is_basic_living_recipient": True},
        },
        "subscription_account": {},
    }
    for key, patch in overrides.items():
        base = doc.setdefault(key, {})
        for inner_key, value in patch.items():
            if isinstance(value, dict) and isinstance(base.get(inner_key), dict):
                base[inner_key].update(value)
            else:
                base[inner_key] = value
    return doc


def test_analyze_rental_needs_more_info_when_core_missing():
    result = rental_engine.analyze_rental({"target_housing": {"track": "rental"}})
    assert result["status"] == "needs_more_info"
    assert result["missing_required_fields"]


def test_analyze_rental_permanent_recipient_headline_and_notes():
    result = rental_engine.analyze_rental(_rental_doc())
    assert result["status"] == "ok"
    assert result["judgments"]["permanent"]["rank"] == 1
    assert result["eligible_types"] == ["permanent"]
    assert "1순위" in result["headline"]
    # full(자산 등) 미입력 → 잠정 판정 + 항상 공고문 대조 안내.
    assert result["confidence"] == "provisional"
    assert any("extract_lease_notice_text" in n for n in result["verification_notes"])


def test_analyze_rental_homeowner_household_is_blocked():
    result = rental_engine.analyze_rental(
        _rental_doc(user_profile={"owned_house_count": 1})
    )
    assert result["blocking"]["is_homeless_household"] is False
    assert result["eligible_types"] == []
    assert result["judgments"]["permanent"]["eligible"] is False


def test_analyze_rental_without_type_screens_all_four():
    doc = _rental_doc(target_housing={"rental_type": None})
    # 유형 미지정 core에는 rental_type이 포함되어 needs_more_info가 되므로,
    # 스크리닝 경로는 rental_type만 비운 완성 문서로 직접 검증한다.
    doc["target_housing"].pop("rental_type")
    doc["subscription_account"] = {"duration_months": 30, "payment_count": 30}
    result = rental_engine.analyze_rental(doc)
    assert result["status"] == "ok"
    assert set(result["judgments"]) == {"permanent", "national", "happy", "public"}
    assert "permanent" in result["eligible_types"]  # 수급자라 영구임대는 확실
```

**설계 배경:** `rental_type`은 core 질문이라 질문 유도(next_questions)에서는 계속 묻지만, **판정에서는 rental_type 누락만으로 막지 않는다** — `analyze_rental`의 core 누락 검사가 `target_housing.rental_type`을 제외하고(`_RENTAL_TYPE_FIELD` 필터, Step 3 참고) 4유형 스크리닝으로 대체한다. 유형을 못 정한 사용자에게 유형 추천이 되는 경로다.

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_rental_engine.py -v`
Expected: FAIL — `AttributeError: ... no attribute 'analyze_rental'`

- [ ] **Step 3: 구현**

`rental_engine.py` 상단 import에 추가:

```python
from .engine import _age_from_birth_date, _years_since
```

본문에 추가:

```python
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
        return {"eligible": False, "rank": None,
                "basis": "무주택 세대구성원 요건 미충족", "notes": []}
    if rental_type != "happy":  # 행복주택 자산은 계층별이라 judge_happy 안에서 검사
        violations = check_assets(rental_type, None, real_estate, car_value, rules)
        if violations:
            return {"eligible": False, "rank": None,
                    "basis": "; ".join(v["reason"] for v in violations), "notes": []}
    if rental_type == "permanent":
        return judge_permanent(
            age=age, income_ratio=income_ratio, household_size=household_size,
            is_basic_living_recipient=user.welfare.is_basic_living_recipient,
            is_national_merit=user.welfare.is_national_merit,
            is_near_poverty=user.welfare.is_near_poverty,
            is_single_parent=user.is_single_parent, rules=rules,
        )
    if rental_type == "national":
        return judge_national(
            income_ratio=income_ratio, household_size=household_size,
            desired_size_sqm=target.desired_size_sqm,
            account_months=account.duration_months or 0,
            payment_count=account.payment_count or 0,
            age=age, dependents_count=user.dependents_count or 0,
            residence_years=user.residence_years_in_region or 0,
            children_count=user.children_count or 0, rules=rules,
        )
    if rental_type == "happy":
        return judge_happy(
            age=age, is_married=user.marriage.is_married, marriage_years=marriage_years,
            infants_count=user.infants_count or 0, is_single_parent=user.is_single_parent,
            is_housing_benefit_recipient=user.welfare.is_housing_benefit_recipient,
            income_ratio=income_ratio, household_size=household_size,
            is_dual_income=user.income_and_assets.is_dual_income,
            real_estate_krw=real_estate, car_value_krw=car_value, rules=rules,
        )
    return judge_public(
        income_ratio=income_ratio, household_size=household_size,
        desired_size_sqm=target.desired_size_sqm,
        account_months=account.duration_months or 0,
        payment_count=account.payment_count or 0,
        target_region=target.target_region or user.residence_area or "", rules=rules,
    )


_TYPE_LABEL = {"permanent": "영구임대", "national": "국민임대", "happy": "행복주택",
               "public": "공공임대"}


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
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_rental_engine.py -v`
Expected: PASS (24 passed)

- [ ] **Step 5: 회귀 확인 후 Commit**

Run: `uv run pytest` — 전체 스위트 PASS 확인.

```bash
uv run ruff format . && git add -u && git commit -m "(feat) 임대 판정 오케스트레이터 — 유형 미정 4유형 스크리닝·headline·잠정판정"
```

---

### Task 10: MCP 도구 `analyze_my_rental` + 등록 + 기존 안내 교체

**Files:**
- Create: `src/slug_mcp/tools/rental_analyze.py`
- Modify: `src/slug_mcp/tools/analyze.py:35-43` (rental 분기 안내 교체)
- Modify: `src/slug_mcp/server.py` (import, `_TOOLS` 등록, instructions 문구)
- Test: `tests/test_rental_engine.py`

**Interfaces:**
- Consumes: `rental_engine.analyze_rental(doc)` (Task 9), `store_module.default_store`
- Produces: MCP 도구 `analyze_my_rental(session_id: str) -> dict[str, Any]`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
import pytest

from slug_mcp import store as store_module
from slug_mcp.store import ProfileStore
from slug_mcp.tools import analyze as analyze_tools
from slug_mcp.tools import rental_analyze as rental_analyze_tools


@pytest.fixture()
def _fresh_store(monkeypatch):
    monkeypatch.setattr(store_module, "default_store", ProfileStore())


def test_analyze_my_rental_full_flow(_fresh_store):
    sid, _ = store_module.default_store.upsert(None, _rental_doc())
    result = rental_analyze_tools.analyze_my_rental(sid)
    assert result["status"] == "ok"
    assert result["judgments"]["permanent"]["rank"] == 1


def test_analyze_my_rental_rejects_missing_session_and_sale_track(_fresh_store):
    assert rental_analyze_tools.analyze_my_rental("no-such")["status"] == "session_not_found"
    sid, _ = store_module.default_store.upsert(None, {"target_housing": {"track": "sale"}})
    assert rental_analyze_tools.analyze_my_rental(sid)["status"] == "not_rental_track"


def test_analyze_my_subscription_now_points_to_analyze_my_rental(_fresh_store):
    sid, _ = store_module.default_store.upsert(None, _rental_doc())
    result = analyze_tools.analyze_my_subscription(sid)
    assert result["status"] == "rental_track"
    assert "analyze_my_rental" in result["guidance"]


def test_server_registers_analyze_my_rental():
    from slug_mcp import server

    registered = [fn.__name__ for fn, _, _ in server._TOOLS]
    assert "analyze_my_rental" in registered
    assert "analyze_my_rental" in server.mcp.instructions
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_rental_engine.py -v`
Expected: FAIL — `ImportError: cannot import name 'rental_analyze'`

- [ ] **Step 3: 구현**

`src/slug_mcp/tools/rental_analyze.py` 생성:

```python
"""세션 프로필을 임대 룰 엔진에 넣어 유형별 자격·순위를 판정하는 도구."""

from __future__ import annotations

from typing import Any

from .. import rental_engine
from .. import store as store_module


def analyze_my_rental(session_id: str) -> dict[str, Any]:
    """저장된 프로필로 임대주택 자격을 판정한다 (네트워크 호출 없는 순수 계산).

    수행 내용: 무주택 세대구성원·자산·자동차 차단필터 → 유형별 판정 — 영구임대(수급자
    순위제), 국민임대(소득 70%컷·통장 순위·동순위 배점), 행복주택(청년·신혼부부·고령자
    등 계층 추론), 공공임대(통장 우선/잔여공급). rental_type을 정하지 않았으면 4유형
    전부를 스크리닝해 신청 가능한 유형 목록을 돌려준다.

    기준은 마이홈포털 2026년도 일반 고시 기준의 참고 기준선(잠정 판정)이며, 단지별
    기준은 공고문이 최종이다 — 결과의 verification_notes에 따라 search_lease_notices로
    공고를 찾고 extract_lease_notice_text로 원문을 대조한다. 무엇을 더 입력하면
    정확해지는지는 action_items로, 한 줄 결론은 headline로 함께 준다.

    Args:
        session_id: update_my_profile이 발급한 세션 ID (target_housing.track='rental' 필요)
    """
    doc = store_module.default_store.get(session_id)
    if doc is None:
        return {
            "status": "session_not_found",
            "guidance": "세션이 없거나 만료되었습니다(보관 24시간). update_my_profile로 "
            "프로필을 만든 뒤 그 session_id로 다시 호출하세요.",
        }
    if (doc.get("target_housing") or {}).get("track") != "rental":
        return {
            "status": "not_rental_track",
            "guidance": "이 도구는 임대 트랙 전용입니다. update_my_profile로 "
            "target_housing.track='rental'을 설정하거나, 분양(청약) 판정은 "
            "analyze_my_subscription을 사용하세요.",
        }
    return rental_engine.analyze_rental(doc)
```

`src/slug_mcp/tools/analyze.py`의 rental 분기(35~43행)를 교체:

```python
    # 임대 트랙은 기준표·판정 구조가 달라 전용 도구로 위임한다.
    if (doc.get("target_housing") or {}).get("track") == "rental":
        return {
            "status": "rental_track",
            "guidance": "임대주택 자격 판정은 analyze_my_rental을 사용하세요. 공고 검색은 "
            "search_lease_notices, 공고문 원문 대조는 extract_lease_notice_text입니다.",
        }
```

`src/slug_mcp/server.py` 수정 2곳:

```python
from .tools import analyze, competition, lh_lease, notices, profile, recommend, rental_analyze
```

`_TOOLS`의 `analyze_my_subscription` 행 아래에 추가:

```python
    (rental_analyze.analyze_my_rental, "임대 자격 판정", _READ_LOCAL),
```

instructions의 임대 안내 문장을 교체 — 기존:

```
"임대주택(영구·국민·행복·공공임대) 상담이면 update_my_profile에 "
"target_housing.track='rental'(+rental_type)을 채우세요 — 임대에 맞는 질문이 "
"안내됩니다. 임대 공고 검색·공고문 원문은 search_lease_notices/"
"extract_lease_notice_text를 사용합니다. "
```

신규:

```
"임대주택(영구·국민·행복·공공임대) 상담이면 update_my_profile에 "
"target_housing.track='rental'(+rental_type)을 채우고 같은 session_id로 "
"analyze_my_rental(자격·순위 잠정판정)을 호출하세요. 임대 공고 검색·공고문 원문은 "
"search_lease_notices/extract_lease_notice_text를 사용합니다. "
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest`
Expected: 전체 스위트 PASS (기존 `tests/test_rental_profile.py`의 rental 안내 테스트가 옛 문구를 단언하면 새 문구로 갱신 — `analyze_tools.analyze_my_subscription` 관련 단언은 `status == "rental_track"`·`"analyze_my_rental" in guidance`로 바꾼다. `profile.py:44-49`의 임대 guidance 문구도 "임대 자격 자동판정은 아직 지원하지 않으므로"를 "analyze_my_rental로 자격·순위를 판정하세요"로 갱신하고 관련 테스트를 맞춘다.)

- [ ] **Step 5: Commit**

```bash
uv run ruff format . && git add -A src tests
git commit -m "(feat) analyze_my_rental 도구 등록 — 임대 4유형 자격·순위 잠정판정"
```

---

### Task 11: 마무리 검증

**Files:**
- Modify: `docs/rental-policy-spec.md` (상태 줄 갱신: "엔진 미구현" → "구현 완료")

- [ ] **Step 1: 전체 검증 실행**

```bash
uv run pytest && uv run ruff format --check . && uv run ruff check .
```
Expected: 전부 PASS / "already formatted" / "All checks passed"

- [ ] **Step 2: 서버 기동 스모크**

Run: `uv run python -c "from slug_mcp import server; print(len(server._TOOLS), '개 도구 등록')"`
Expected: `11 개 도구 등록` (기존 10 + analyze_my_rental)

- [ ] **Step 3: 스펙 상태 갱신 및 Commit**

`docs/rental-policy-spec.md` 2행을 `상태: 구현 완료(2026-07-13). 요검증 4건은 실공고 대조 대기.`로 수정.

```bash
git add -f docs/rental-policy-spec.md && git commit -m "(docs) 임대 판정 스펙 상태 갱신"
```

- [ ] **Step 4 (후속, 이 플랜 밖):** 요검증 4건 — `extract_lease_notice_text`로 2026년 실공고 4종(유형별 1건)을 추출해 영구 2순위 가산·영구 자산 상한·행복 고령자/수급자 자산·8인 이상 소득을 대조하고 `rental_rules.yaml` 주석의 "요검증"을 확정 표기로 바꾼다. API 키·실공고 선택이 필요해 별도 세션에서 진행.

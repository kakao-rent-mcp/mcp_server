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

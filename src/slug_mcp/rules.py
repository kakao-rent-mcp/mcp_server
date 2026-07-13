"""config/*.yaml 로더 (분양 eligibility_rules / 임대 rental_rules).

scoring.py / engine.py 가 공유한다. 값 자체의 근거·검증 상태는
docs/subscription-policy-spec.md / docs/rental-policy-spec.md 의 검증 태그를 본다.
"""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from typing import Any

import yaml


@lru_cache(maxsize=1)
def load_rules() -> dict[str, Any]:
    path = resources.files("slug_mcp.config").joinpath("eligibility_rules.yaml")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@lru_cache(maxsize=1)
def load_rental_rules() -> dict[str, Any]:
    """config/rental_rules.yaml (임대 4유형 기준표) 로더. 근거는 docs/rental-policy-spec.md."""
    path = resources.files("slug_mcp.config").joinpath("rental_rules.yaml")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)

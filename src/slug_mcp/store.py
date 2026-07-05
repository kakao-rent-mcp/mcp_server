"""세션ID 키 기반 인메모리 NoSQL 문서 스토어.

배포 환경(KC 클라우드)에 RDS를 붙일 수 없어 외부 DB 없이 프로세스 메모리에
사용자 프로필 문서를 보관한다. 특성:

- 문서 지향(스키마리스 dict) — MCP 클라이언트가 대화 몇 번에 걸쳐 조각조각
  보내는 부분 업데이트를 deep merge로 흡수한다.
- TTL(기본 24시간) — 개인 소득·자산 정보를 무기한 들고 있지 않는다.
- 용량 상한(LRU) — 무한 증식으로 컨테이너 메모리가 터지지 않게 가장 오래
  접근된 세션부터 밀어낸다.
- 프로세스 재시작 시 소멸한다. MCP transport 자체는 stateless(PlayMCP 권장)를
  유지하고, 애플리케이션 수준의 상태만 세션ID로 이어 붙이는 구조다.
"""

from __future__ import annotations

import copy
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

_DEFAULT_TTL_SECONDS = 24 * 60 * 60
_DEFAULT_MAX_SESSIONS = 10_000


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """patch의 값을 base에 재귀적으로 병합한다. dict가 아닌 값은 덮어쓴다."""
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


class ProfileStore:
    """스레드 안전한 인메모리 문서 스토어."""

    def __init__(
        self,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
        max_sessions: int = _DEFAULT_MAX_SESSIONS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._max_sessions = max_sessions
        self._clock = clock
        self._lock = threading.Lock()
        # session_id -> (마지막 접근 시각, 문서). OrderedDict 순서 = LRU 순서.
        self._docs: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()

    def upsert(self, session_id: str | None, patch: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """문서를 부분 갱신(deep merge)하고 (세션ID, 갱신된 문서 사본)을 돌려준다.

        session_id가 None이면 새 세션을 만든다. 알 수 없는(만료된) 세션ID가
        들어오면 그 ID 그대로 새 문서를 만들어, 클라이언트가 들고 있던 ID가
        계속 유효하게 한다.
        """
        with self._lock:
            self._purge_expired()
            sid = session_id or uuid.uuid4().hex[:16]
            _, current = self._docs.get(sid, (0.0, {}))
            merged = _deep_merge(current, copy.deepcopy(patch))
            self._docs[sid] = (self._clock(), merged)
            self._docs.move_to_end(sid)
            while len(self._docs) > self._max_sessions:
                self._docs.popitem(last=False)
            return sid, copy.deepcopy(merged)

    def get(self, session_id: str) -> dict[str, Any] | None:
        """세션 문서 사본을 돌려준다. 없거나 만료됐으면 None."""
        with self._lock:
            self._purge_expired()
            entry = self._docs.get(session_id)
            if entry is None:
                return None
            return copy.deepcopy(entry[1])

    def delete(self, session_id: str) -> bool:
        with self._lock:
            return self._docs.pop(session_id, None) is not None

    def _purge_expired(self) -> None:
        deadline = self._clock() - self._ttl
        expired = [sid for sid, (touched, _) in self._docs.items() if touched < deadline]
        for sid in expired:
            del self._docs[sid]


# 서버 프로세스 전역에서 공유하는 기본 스토어 인스턴스.
default_store = ProfileStore()

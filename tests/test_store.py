"""인메모리 세션 프로필 스토어(store.py) 테스트.

배포 환경에 RDS를 붙일 수 없어 세션ID 키 기반의 인메모리 NoSQL 문서 스토어를 쓴다.
부분 업데이트(deep merge), TTL 만료, 용량 상한(LRU)이 핵심 계약이다.
"""

from __future__ import annotations

from slug_mcp.store import ProfileStore


def test_upsert_without_session_id_creates_new_session():
    store = ProfileStore()
    session_id, doc = store.upsert(None, {"user_profile": {"age": 34}})
    assert isinstance(session_id, str) and len(session_id) >= 8
    assert doc["user_profile"]["age"] == 34


def test_upsert_deep_merges_nested_fields():
    store = ProfileStore()
    session_id, _ = store.upsert(
        None, {"user_profile": {"age": 34, "marriage": {"is_married": True}}}
    )
    _, doc = store.upsert(
        session_id, {"user_profile": {"marriage": {"marriage_date": "2021-03-10"}}}
    )
    # 기존 값은 유지되고 새 값만 병합되어야 한다
    assert doc["user_profile"]["age"] == 34
    assert doc["user_profile"]["marriage"]["is_married"] is True
    assert doc["user_profile"]["marriage"]["marriage_date"] == "2021-03-10"


def test_upsert_overwrites_scalar_values():
    store = ProfileStore()
    session_id, _ = store.upsert(None, {"user_profile": {"age": 34}})
    _, doc = store.upsert(session_id, {"user_profile": {"age": 35}})
    assert doc["user_profile"]["age"] == 35


def test_get_unknown_session_returns_none():
    store = ProfileStore()
    assert store.get("no-such-session") is None


def test_upsert_with_unknown_session_id_recreates_it():
    """만료 등으로 사라진 세션ID로 업데이트해도 같은 ID로 새 문서를 만들어준다."""
    store = ProfileStore()
    session_id, doc = store.upsert("client-kept-id", {"user_profile": {"age": 40}})
    assert session_id == "client-kept-id"
    assert store.get("client-kept-id") == doc


def test_delete_session():
    store = ProfileStore()
    session_id, _ = store.upsert(None, {"user_profile": {"age": 34}})
    assert store.delete(session_id) is True
    assert store.get(session_id) is None
    assert store.delete(session_id) is False


def test_ttl_expiry():
    now = [1000.0]
    store = ProfileStore(ttl_seconds=60, clock=lambda: now[0])
    session_id, _ = store.upsert(None, {"user_profile": {"age": 34}})
    now[0] += 59
    assert store.get(session_id) is not None
    now[0] += 2  # TTL 초과
    assert store.get(session_id) is None


def test_access_refreshes_ttl():
    now = [1000.0]
    store = ProfileStore(ttl_seconds=60, clock=lambda: now[0])
    session_id, _ = store.upsert(None, {"user_profile": {"age": 34}})
    now[0] += 50
    store.upsert(session_id, {"user_profile": {"age": 35}})  # 갱신 시 TTL 리셋
    now[0] += 50
    assert store.get(session_id) is not None


def test_max_sessions_evicts_oldest():
    store = ProfileStore(max_sessions=2)
    first, _ = store.upsert(None, {"a": 1})
    second, _ = store.upsert(None, {"b": 2})
    third, _ = store.upsert(None, {"c": 3})
    assert store.get(first) is None  # 가장 오래된 세션이 밀려난다
    assert store.get(second) is not None
    assert store.get(third) is not None


def test_returned_doc_is_a_copy():
    """반환된 문서를 바깥에서 고쳐도 스토어 내부 상태가 오염되지 않아야 한다."""
    store = ProfileStore()
    session_id, doc = store.upsert(None, {"user_profile": {"age": 34}})
    doc["user_profile"]["age"] = 99
    assert store.get(session_id)["user_profile"]["age"] == 34

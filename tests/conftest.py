import pytest


@pytest.fixture(autouse=True)
def _fake_service_keys(monkeypatch):
    """실제 API 호출을 흉내내는 테스트에서 서비스키 누락 오류가 나지 않도록 더미 값을 넣는다."""
    monkeypatch.setenv("DECODING_KEY", "test-decoding-key")
    monkeypatch.setenv("ENCODING_KEY", "test-encoding-key")

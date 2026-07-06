"""PlayMCP 심사 규격 준수 테스트 (docs/playmcp-guidelines.md).

- 서버·도구 이름에 'kakao' 금지 (대소문자 불문)
- 툴 이름: 영문/숫자/_/- 만, 1~128자
- 툴 개수: 3~10개 권장, 20개 초과 금지
- annotations 5종(title/readOnlyHint/destructiveHint/openWorldHint/idempotentHint) 모두 지정
- description: 서비스명(국·영문 병기) 포함, 1,024자 이내
"""

from __future__ import annotations

import re

import pytest

from slug_mcp.server import SERVICE_NAME, mcp


@pytest.fixture(scope="module")
async def tools():
    return await mcp.list_tools()


async def test_tool_count_within_recommended_range(tools):
    assert 3 <= len(tools) <= 10


async def test_expected_tools_registered(tools):
    names = {tool.name for tool in tools}
    assert names == {
        "search_housing_notices",
        "get_notice_detail",
        "search_lease_notices",
        "get_lease_notice_detail",
        "extract_lease_notice_text",
        "get_competition_stats",
        "update_my_profile",
        "get_my_profile",
        "analyze_my_subscription",
        "recommend_housing",
    }


async def test_no_kakao_in_server_or_tool_names(tools):
    assert "kakao" not in mcp.name.lower()
    for tool in tools:
        assert "kakao" not in tool.name.lower()


async def test_tool_names_follow_charset_rule(tools):
    for tool in tools:
        assert re.fullmatch(r"[A-Za-z0-9_-]{1,128}", tool.name)


async def test_all_tools_have_five_annotations(tools):
    for tool in tools:
        annotations = tool.annotations
        assert annotations is not None, f"{tool.name}: annotations 누락"
        assert annotations.title, f"{tool.name}: title 누락"
        for hint in ("readOnlyHint", "destructiveHint", "openWorldHint", "idempotentHint"):
            assert getattr(annotations, hint) is not None, f"{tool.name}: {hint} 누락"


async def test_descriptions_include_service_name_and_fit_limit(tools):
    for tool in tools:
        assert tool.description, f"{tool.name}: description 누락"
        assert SERVICE_NAME in tool.description, f"{tool.name}: 서비스명 병기 누락"
        assert len(tool.description) <= 1024, f"{tool.name}: description 1,024자 초과"


async def test_profile_write_tool_is_not_marked_read_only(tools):
    """update_my_profile은 세션 상태를 바꾸므로 readOnlyHint=False여야 한다."""
    by_name = {tool.name: tool for tool in tools}
    assert by_name["update_my_profile"].annotations.readOnlyHint is False
    assert by_name["get_my_profile"].annotations.readOnlyHint is True

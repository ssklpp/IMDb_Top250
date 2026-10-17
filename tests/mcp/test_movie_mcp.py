"""MCP 서버(movie_mcp/server.py)를 메모리 안에서 띄워 검사한다. 외부 API·OpenAI는 부르지 않는다.

mcp·langchain이 필요해서 tests/unit(pytest만 설치하는 CI python 잡)이 아니라 여기에 둔다.
CI에서는 전체 의존성을 설치하는 deps 잡이 돌린다.

    pytest tests/mcp/ -q
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from langchain_core.tools import StructuredTool  # noqa: E402
from langchain_core.tools.retriever import create_retriever_tool  # noqa: E402
from langchain_core.utils.function_calling import convert_to_openai_tool  # noqa: E402
from langchain_mcp_adapters.tools import load_mcp_tools  # noqa: E402
from mcp.shared.memory import create_connected_server_and_client_session  # noqa: E402

from movie_mcp.server import mcp  # noqa: E402
from movie_tools import imdb, kobis, web  # noqa: E402
from sources import extract_sources  # noqa: E402


def run(coro_fn):
    async def go():
        async with create_connected_server_and_client_session(mcp._mcp_server) as session:
            return await coro_fn(session)

    return asyncio.run(go())


def _local_definitions() -> dict:
    """agent.local_tools()와 같은 방식으로 만든 도구 정의. agent를 import하면 LLM 클라이언트를
    만들어 OpenAI 키가 필요하므로 같은 생성 코드를 여기서 재현한다(검색기는 아무 것이나 된다)."""

    from langchain_core.retrievers import BaseRetriever

    class Dummy(BaseRetriever):
        def _get_relevant_documents(self, query, *, run_manager):
            return []

    tools = [
        create_retriever_tool(
            Dummy(), name=imdb.SEARCH_NAME, description=imdb.SEARCH_DESCRIPTION,
            response_format="content_and_artifact",
        ),
        StructuredTool.from_function(imdb.run_sql, name=imdb.SQL_NAME, description=imdb.SQL_DESCRIPTION),
        StructuredTool.from_function(web.web_search, name=web.NAME, description=web.DESCRIPTION),
        StructuredTool.from_function(
            kobis.kobis_search, name=kobis.NAME, description=kobis.DESCRIPTION, args_schema=kobis.KobisInput
        ),
    ]
    return {t.name: convert_to_openai_tool(t) for t in tools}


def test_도구_정의가_로컬_도구와_같다():
    """MCP로 바꿔도 모델이 받는 도구 정의(이름·설명·인자 스키마)가 한 글자도 달라지지 않는지.

    달라지면 같은 질문에 다른 도구를 고르거나 인자를 다르게 써서, 전환 전후 평가 결과가 갈린다.
    설명을 movie_tools 상수 한 곳에 둔 이유이고, kobis 설명의 들여쓰기 하나로도 실제로 달랐다.
    """
    async def go(session):
        return {t.name: convert_to_openai_tool(t) for t in await load_mcp_tools(session)}

    remote = run(go)
    local = _local_definitions()
    assert sorted(remote) == ["imdb_search", "imdb_sql", "kobis_search", "web_search"]
    for name in local:
        assert json.dumps(remote[name], sort_keys=True) == json.dumps(local[name], sort_keys=True), name


def test_kobis_search_type은_허용값만_받는다():
    """search_type이 enum으로 노출되는지. 빠지면 모델이 'boxoffice' 같은 값을 지어낸다."""
    async def go(session):
        tools = (await session.list_tools()).tools
        return next(t for t in tools if t.name == "kobis_search").inputSchema

    schema = run(go)
    assert schema["properties"]["search_type"]["enum"] == [
        "movie", "detail", "daily", "weekly", "weekend", "weekday",
    ]


def test_잘못된_날짜는_KOBIS를_부르지_않고_INVALID_DATE(monkeypatch):
    """박스오피스 모드의 날짜 검증이 MCP 경로에서도 도는지. 7자리 날짜를 2026-09-01로 통과시킨 적이 있다."""
    monkeypatch.setenv("KOBIS_API_KEY", "test-key")

    def no_network(*a, **k):
        raise AssertionError("KOBIS를 호출하면 안 된다")

    monkeypatch.setattr(kobis, "_kobis_get", no_network)

    async def go(session):
        return await session.call_tool("kobis_search", {"query": "2026091", "search_type": "daily"})

    result = run(go)
    assert not result.isError
    assert result.content[0].text.startswith("[TOOL_ERROR code=INVALID_DATE]")


def test_연도_형식_오류는_에러_결과로_돌아간다(monkeypatch):
    """KobisInput의 field_validator(연도 4자리)가 MCP 경로에서도 도는지.

    시그니처만으로는 형태만 검사돼서 '24' 같은 연도가 그대로 KOBIS로 갔다. 에러 결과(isError)로
    돌려줘야 모델이 인자를 고쳐 다시 부른다.
    """
    monkeypatch.setenv("KOBIS_API_KEY", "test-key")
    monkeypatch.setattr(kobis, "_kobis_get", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))

    async def go(session):
        return await session.call_tool("kobis_search", {"query": "기생충", "open_start_dt": "24"})

    result = run(go)
    assert result.isError
    assert "4자리" in result.content[0].text


def test_허용되지_않은_search_type은_에러_결과():
    async def go(session):
        return await session.call_tool("kobis_search", {"query": "기생충", "search_type": "boxoffice"})

    assert run(go).isError


def test_SQL_도구는_MCP로도_읽기_전용이다():
    """imdb_data.query_movies의 실행 제한이 MCP 경로에서도 그대로인지. 모델이 쓴 SQL이 그대로 들어온다."""
    async def go(session):
        ok = await session.call_tool("imdb_sql", {"query": "SELECT title FROM movies WHERE rank = 1"})
        bad = await session.call_tool("imdb_sql", {"query": "DROP TABLE movies"})
        return ok, bad

    ok, bad = run(go)
    assert "The Shawshank Redemption" in ok.content[0].text
    assert bad.content[0].text.startswith("[TOOL_ERROR code=INVALID_QUERY]")


def test_SQL_결과는_출처_칩이_되고_에러는_안_된다():
    """어댑터를 거친 ToolMessage 형태(content가 블록 목록)에서 sources가 출처를 뽑는지.

    MCP 도구의 content는 문자열이 아니라 [{"type": "text", ...}]다. 문자열만 보던 시절의 코드는
    에러 응답에도 고정 출처를 붙였다.
    """
    async def go(session):
        tool = next(t for t in await load_mcp_tools(session) if t.name == "imdb_sql")
        call = {"name": "imdb_sql", "type": "tool_call", "id": "1"}
        ok = await tool.ainvoke({**call, "args": {"query": "SELECT title FROM movies LIMIT 1"}})
        bad = await tool.ainvoke({**call, "args": {"query": "DELETE FROM movies"}})
        return ok, bad

    ok, bad = run(go)
    assert isinstance(ok.content, list)
    assert extract_sources("imdb_sql", ok) == [
        {"tool": "imdb_sql", "label": "IMDB Top 250 목록 (SQL 조회)", "url": None}
    ]
    assert extract_sources("imdb_sql", bad) == []


def test_스키마_리소스():
    async def go(session):
        return await session.read_resource("imdb://schema")

    from imdb_data import SQL_SCHEMA

    assert run(go).contents[0].text == SQL_SCHEMA


@pytest.mark.parametrize("name", ["imdb_search", "imdb_sql", "kobis_search", "web_search"])
def test_도구_이름이_그대로다(name):
    """page.tsx의 "검색 중" 라벨, sources.py, 골든셋 expected_tools가 이 이름에 묶여 있다."""
    async def go(session):
        return [t.name for t in (await session.list_tools()).tools]

    assert name in run(go)


def test_KOBIS_주간_원본은_도구가_아니라_리소스다(monkeypatch):
    """브리핑은 숫자를 직접 계산하려고 KOBIS 원본이 필요하다. 이걸 도구로 내보내면 에이전트(LLM)의 도구 목록에도
    올라가 챗봇의 도구 선택과 평가가 바뀐다. 그래서 리소스 템플릿으로만 내보내고, 도구는 4개 그대로여야 한다."""
    import json as _json

    fixture = _json.loads(
        (Path(__file__).resolve().parents[1] / "fixtures" / "kobis_weekly_20261011.json").read_text(encoding="utf-8")
    )
    asked = []
    monkeypatch.setattr(kobis, "fetch_weekly_raw", lambda d: asked.append(d) or fixture)

    async def go(session):
        templates = (await session.list_resource_templates()).resourceTemplates
        result = await session.read_resource("kobis://weekly/20261011")
        tools = (await session.list_tools()).tools
        return templates, result, tools

    templates, result, tools = run(go)
    assert any(t.uriTemplate == "kobis://weekly/{target_dt}" for t in templates)
    assert _json.loads(result.contents[0].text)["boxOfficeResult"]["showRange"] == "20261005~20261011"
    assert asked == ["20261011"]
    assert len(tools) == 4

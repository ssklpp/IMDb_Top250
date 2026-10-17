"""영화 데이터 도구 4개를 MCP로 내보낸다.

    python -m movie_mcp.server                         # stdio (Claude Desktop 등)
    python -m movie_mcp.server --transport streamable-http --host 0.0.0.0 --port 8001

구현은 movie_tools에 있고, 여기서는 MCP 도구로 등록만 한다. 이름·설명·인자가 에이전트의 로컬 도구와
같아야 모델 입력이 같다 — tests/mcp/test_movie_mcp.py가 비교한다.

에이전트(server.py)는 MCP_URL이 있으면 이 서버의 도구를 쓴다. 그러면 KOBIS·Tavily 키와 벡터스토어는
이 프로세스만 갖는다. 질문을 임베딩해야 하므로 OPENAI_API_KEY는 여기에도 필요하다.
"""

import argparse
import json
import os
from pathlib import Path
from typing import Annotated

import anyio
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from imdb_data import SQL_SCHEMA
from logging_config import configure_logging, get_logger
from movie_tools import imdb, kobis, web

log = get_logger("movie_mcp")

# Claude Desktop은 이 서버를 어느 폴더에서 띄울지 모른다. 작업 폴더가 아니라 저장소의 .env를 읽는다.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _transport_security() -> TransportSecuritySettings:
    """Host·Origin 헤더 검사(DNS 리바인딩 방어)를 항상 켠다.

    FastMCP는 host가 127.0.0.1일 때만 이 검사를 자동으로 켠다. 컨테이너에서 0.0.0.0으로 띄우면
    꺼지는데, 그렇다고 다 열지 않고 compose 서비스 이름(MCP_ALLOWED_HOSTS=mcp:*)만 더한다.
    브라우저에서 부를 일이 없으므로 Origin은 localhost만 허용한다.
    """
    extra = [h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", *extra],
        allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
    )


mcp = FastMCP("movie-data", transport_security=_transport_security())

# 도구 본체는 requests로 외부 API를 부르는 동기 함수다. FastMCP는 동기 도구를 이벤트 루프에서 그대로
# 실행하므로, 감싸지 않으면 KOBIS 응답을 기다리는 동안 다른 요청이 전부 멈춘다. 그래서 스레드로 넘긴다.


@mcp.tool(name=imdb.SEARCH_NAME, description=imdb.SEARCH_DESCRIPTION)
async def imdb_search(
    query: Annotated[str, Field(description=imdb.SEARCH_QUERY_DESCRIPTION)],
) -> CallToolResult:
    text, movies = await anyio.to_thread.run_sync(imdb.search_movies, query)
    # 모델은 content(본문)만 본다. structuredContent의 영화 목록은 출처 칩용이다 — 로컬 도구에서
    # Document 메타데이터가 하던 일이다(sources._imdb_sources).
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structuredContent={"movies": movies},
    )


@mcp.tool(name=imdb.SQL_NAME, description=imdb.SQL_DESCRIPTION)
async def imdb_sql(query: str) -> str:
    return await anyio.to_thread.run_sync(imdb.run_sql, query)


@mcp.tool(name=web.NAME, description=web.DESCRIPTION)
async def web_search(query: str) -> str:
    return await anyio.to_thread.run_sync(web.web_search, query)


_K = kobis.KobisInput.model_fields


@mcp.tool(name=kobis.NAME, description=kobis.DESCRIPTION)
async def kobis_search(
    # 인자 설명·제약은 KobisInput에서 가져온다. 기본값은 Annotated 안에 둘 수 없어 시그니처에 둔다.
    query: Annotated[str, Field(min_length=1, description=_K["query"].description)],
    open_start_dt: Annotated[str, Field(description=_K["open_start_dt"].description)] = "",
    open_end_dt: Annotated[str, Field(description=_K["open_end_dt"].description)] = "",
    search_type: Annotated[kobis.SearchType, Field(description=_K["search_type"].description)] = "movie",
) -> str:
    # 시그니처는 형태만 검사한다. 연도 4자리·공백 제거 같은 규칙(field_validator)은 KobisInput을
    # 거쳐야 돈다. 실패하면 ValidationError가 MCP 에러 결과(isError)로 모델에 간다 — 로컬 도구에서
    # LangChain이 ValidationError를 돌려주던 것과 같은 동작이다.
    args = kobis.KobisInput(
        query=query, open_start_dt=open_start_dt, open_end_dt=open_end_dt, search_type=search_type
    )
    return await anyio.to_thread.run_sync(lambda: kobis.kobis_search(**args.model_dump()))


@mcp.resource(
    "kobis://weekly/{target_dt}",
    name="kobis_weekly",
    description="KOBIS 주간(월~일) 박스오피스 원본 JSON. target_dt는 그 주에 속한 YYYYMMDD. 브리핑용",
    mime_type="application/json",
)
async def kobis_weekly(target_dt: str) -> str:
    # 도구가 아니라 리소스다. 도구로 만들면 에이전트(LLM)의 도구 목록에도 올라가 챗봇의 도구 선택과 평가가 바뀐다.
    raw = await anyio.to_thread.run_sync(kobis.fetch_weekly_raw, target_dt)
    return json.dumps(raw, ensure_ascii=False)


@mcp.resource("imdb://schema", name="imdb_schema", description="imdb_sql이 조회하는 SQLite 테이블 스키마")
def imdb_schema() -> str:
    return SQL_SCHEMA


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    """compose 헬스체크용. 벡터스토어가 준비돼야 200이다(api는 이게 200이 된 뒤에 뜬다)."""
    checks = {
        "vectorstore": imdb.vector_count() > 0,
        "openai_key": bool(os.environ.get("OPENAI_API_KEY")),
        "kobis_key": bool(os.environ.get("KOBIS_API_KEY")),
        "tavily_key": bool(os.environ.get("TAVILY_API_KEY")),
    }
    ok = checks["vectorstore"] and checks["openai_key"]
    return JSONResponse(
        {"status": "ok" if ok else "unhealthy", "checks": checks}, status_code=200 if ok else 503
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="영화 데이터 MCP 서버")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()

    # 로그는 stderr로 간다(logging_config). stdio 모드에서는 stdout이 MCP 메시지 통로라
    # 여기에 다른 것을 쓰면 클라이언트가 깨진다. print() 금지가 여기서는 특히 중요하다.
    configure_logging()
    imdb.load_vectorstore()
    log.info("mcp.start", transport=args.transport, tools=4)

    if args.transport == "stdio":
        mcp.run("stdio")
    else:
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run("streamable-http")


if __name__ == "__main__":
    main()

import asyncio
import hashlib
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
import httpx
from cachetools import TTLCache
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel, Field, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from agent import CHECKPOINT_DB_PATH, build_agent
from movie_tools import imdb
from kobis_format import format_date_context, today_kst
from logging_config import configure_logging, get_logger, request_id_var, session_id_var
from ratelimit import from_proxy, rate_limit_key
from sources import extract_sources

configure_logging()
log = get_logger("server")

REQUEST_TIMEOUT_S = 120

# 영화 질문은 길 이유가 없다. 넉넉히 잡되 상한은 둔다.
MAX_QUESTION_CHARS = 2000
# UUID(36자)가 정상 경로. 여유를 두되 무한정 긴 키가 들어오는 것은 막는다.
MAX_SESSION_ID_CHARS = 64

# 있으면 도구를 이 MCP 서버(movie_mcp/server.py)에서 받는다(compose: http://mcp:8001/mcp).
# 없으면 같은 프로세스의 로컬 도구를 쓴다 — Railway 배포가 이 경로다.
MCP_URL = os.environ.get("MCP_URL", "").strip()
EXPECTED_TOOLS = {"imdb_search", "imdb_sql", "kobis_search", "web_search"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """SQLite 체크포인터 연결을 앱 생명주기에 묶는다.

    AsyncSqliteSaver를 쓰는 이유: 이 서버는 astream_events(async)로 실행되는데
    동기 SqliteSaver는 aput/aget_tuple에서 NotImplementedError를 던진다.
    파일 기반이라 프로세스가 재시작돼도 사용자 대화가 유지된다.
    """
    Path(CHECKPOINT_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(CHECKPOINT_DB_PATH)
    saver = AsyncSqliteSaver(conn)
    await saver.setup()
    log.info("startup.checkpointer_ready", db=CHECKPOINT_DB_PATH)

    if MCP_URL:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        # 세션을 붙잡아 두지 않는다. get_tools()가 만든 도구는 호출마다 새 세션을 열어서
        # MCP 서버가 재시작돼도 다음 호출부터 다시 붙는다.
        client = MultiServerMCPClient({"movie": {"transport": "streamable_http", "url": MCP_URL}})
        tools = await client.get_tools()
        log.info("startup.mcp_tools", url=MCP_URL, tools=sorted(t.name for t in tools))
    else:
        tools = None  # build_agent가 local_tools()로 벡터스토어까지 불러온다
    app.state.tool_names = sorted(t.name for t in tools) if tools else sorted(EXPECTED_TOOLS)
    app.state.agent = build_agent(saver, tools=tools)
    try:
        yield
    finally:
        await conn.close()
        log.info("shutdown.checkpointer_closed")


app = FastAPI(lifespan=lifespan)

_cors_origins = os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Vercel 프록시(route.ts)와 같은 값을 설정해야 방문자 IP 헤더(X-Client-IP)를 믿는다. 없으면 무시한다.
PROXY_SECRET = os.environ.get("PROXY_SECRET", "").strip() or None


def _rate_key(request: Request) -> str:
    """TCP 접속 주소(get_remote_address)로 세지 않는다 — 배포에서는 Railway 프록시 주소라 요청마다
    달라서 제한이 전혀 걸리지 않았다(docs/bug-log.md B-009). 판단 규칙은 ratelimit.py."""
    key, _ = rate_limit_key(
        request.headers, request.client.host if request.client else None, PROXY_SECRET
    )
    return key


limiter = Limiter(key_func=_rate_key)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

_response_cache: TTLCache = TTLCache(maxsize=256, ttl=3600)


class QuestionRequest(BaseModel):
    """사용자 입력 검증.

    도구 인자(KobisInput)는 엄격히 검증하면서 정작 사용자 입력은 무검증이던
    역전 구조를 바로잡는다. 제한이 없으면 빈 질문이 그대로 LLM 호출까지 가고,
    거대한 입력이 레이트리밋 안에서도 OpenAI 비용 공격이 된다.
    """

    question: str = Field(..., min_length=1, max_length=MAX_QUESTION_CHARS)
    # thread_id로 그대로 쓰여 체크포인터의 영구 키가 되므로 길이와 문자를 제한한다.
    session_id: str | None = Field(
        None, min_length=1, max_length=MAX_SESSION_ID_CHARS, pattern=r"^[A-Za-z0-9_-]+$"
    )

    @field_validator("question")
    @classmethod
    def _strip_question(cls, v: str) -> str:
        # min_length=1은 "   " 같은 공백만 있는 입력을 걸러내지 못한다.
        v = v.strip()
        if not v:
            raise ValueError("질문이 비어있습니다.")
        return v


def _error_sentinel(code: str, message: str) -> str:
    """Frontend가 파싱하는 에러 센티넬. code|message 형태."""
    return f"\x1ferror:{code}|{message}\n"


@app.get("/health")
async def health(response: Response):
    """의존성을 실제로 확인한다.

    이전에는 무조건 {"status":"ok"}를 반환해 프로세스 생존만 보장했다.
    railway.toml의 healthcheckPath가 이걸 신뢰하는데, API 키가 빠지거나
    벡터스토어가 비어 있어도 계속 ok를 주고 있었다.

    치명적 문제와 부분 장애를 구분한다:
    - 에이전트 미준비 / 벡터스토어 비어있음 / OPENAI_API_KEY 없음 → 503 (서비스 불가)
    - KOBIS·Tavily 키 없음 → 200 degraded (해당 도구만 실패하고 LLM이 우회한다)

    외부 API(KOBIS·Tavily·OpenAI)는 부르지 않는다. 헬스체크가 외부 장애에 물려 같이 죽으면 안 된다.

    MCP를 쓰면(MCP_URL) 벡터스토어와 KOBIS·Tavily 키는 MCP 서버 쪽에 있으므로 여기서 보지 않는다.
    대신 도구 4개를 받았는지(치명적)와 MCP 서버가 지금 응답하는지(부분 장애)를 본다. MCP 서버는
    같은 compose 네트워크의 내부 서비스라 1초 안에 답하지 못하면 degraded로 본다.
    """
    agent_ready = getattr(app.state, "agent", None) is not None
    vector_count = imdb.vector_count()

    critical = {
        "agent": agent_ready,
        "openai_key": bool(os.environ.get("OPENAI_API_KEY")),
    }
    if MCP_URL:
        critical["mcp_tools"] = set(getattr(app.state, "tool_names", [])) == EXPECTED_TOOLS
        optional = {"mcp_reachable": await _mcp_reachable()}
    else:
        critical["vectorstore"] = vector_count > 0
        optional = {
            "kobis_key": bool(os.environ.get("KOBIS_API_KEY")),
            "tavily_key": bool(os.environ.get("TAVILY_API_KEY")),
        }

    if not all(critical.values()):
        status = "unhealthy"
        response.status_code = 503
    elif not all(optional.values()):
        status = "degraded"
    else:
        status = "ok"

    if status != "ok":
        log.warning(
            "health.not_ok",
            status=status,
            failed=[k for k, v in {**critical, **optional}.items() if not v],
        )

    return {
        "status": status,
        "checks": {**critical, **optional},
        "vectorstore_chunks": vector_count,
        "tools": "mcp" if MCP_URL else "local",
    }


async def _mcp_reachable() -> bool:
    """MCP 서버의 /health가 200인지. MCP_URL(…/mcp)과 같은 호스트에 있다."""
    health_url = httpx.URL(MCP_URL).copy_with(path="/health")
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            return (await client.get(health_url)).status_code == 200
    except httpx.HTTPError:
        return False


@app.post("/api/chat")
@limiter.limit("10/minute")
async def chat(request: Request, req: QuestionRequest):
    # 비밀값이 설정된 서버(배포)는 프록시(route.ts)를 거친 요청만 받는다. 백엔드 주소로 바로 오는 요청은
    # 방문자를 구분할 방법이 없어 레이트리밋이 걸리지 않는다 — LLM 비용 공격의 통로다(B-009).
    if PROXY_SECRET and not from_proxy(request.headers, PROXY_SECRET):
        log.warning("chat.forbidden_direct")
        return JSONResponse(
            {"code": "FORBIDDEN", "error": "웹 화면을 통해 질문해 주세요."}, status_code=403
        )

    request_id = str(uuid.uuid4())
    session_was_new = req.session_id is None
    session_id = req.session_id or str(uuid.uuid4())
    session_id_var.set(session_id)
    request_id_var.set(request_id)

    config = {"configurable": {"thread_id": session_id}}
    cache_key = hashlib.sha256(req.question.strip().lower().encode()).hexdigest()
    started = time.monotonic()

    log.info(
        "chat.request",
        question_len=len(req.question),
        new_session=session_was_new,
        # 방문자 IP 원문은 남기지 않는다. 어떤 근거로 셌는지만 남겨 프록시 설정이 맞는지 확인한다.
        rate_key_source=rate_limit_key(
            request.headers, request.client.host if request.client else None, PROXY_SECRET
        )[1],
    )

    if session_was_new and cache_key in _response_cache:
        cached = _response_cache[cache_key]
        log.info("chat.cache_hit", bytes=len(cached))

        async def stream_cached():
            yield cached

        return StreamingResponse(
            stream_cached(),
            media_type="text/plain; charset=utf-8",
            headers={
                "X-Cache": "HIT",
                "X-Request-Id": request_id,
                "X-Session-Id": session_id,
            },
        )

    async def generate():
        # ContextVars don't auto-propagate to async generators in all runtimes,
        # so we re-bind them here.
        session_id_var.set(session_id)
        request_id_var.set(request_id)

        buffer = []
        tool_calls = 0
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_S):
                async for event in request.app.state.agent.astream_events(
                    # 날짜는 요청마다 새로 넣는다. 에이전트 생성 시점에 넣으면
                    # 서버가 며칠 연속 실행되는 동안 날짜가 굳는다.
                    {
                        "messages": [
                            SystemMessage(content=format_date_context(today_kst())),
                            HumanMessage(content=req.question),
                        ]
                    },
                    config=config,
                    version="v2",
                ):
                    kind = event["event"]

                    if kind == "on_tool_start":
                        tool_name = event.get("name", "unknown")
                        tool_calls += 1
                        log.info("chat.tool_start", tool=tool_name)
                        yield f"\x1ftool:{tool_name}\n"

                    elif kind == "on_tool_end":
                        tool_name = event.get("name", "unknown")
                        log.info("chat.tool_end", tool=tool_name)
                        sources = extract_sources(
                            tool_name, event.get("data", {}).get("output")
                        )
                        if sources:
                            log.info(
                                "chat.sources", tool=tool_name, count=len(sources)
                            )
                            line = f"\x1fsources:{json.dumps(sources, ensure_ascii=False)}\n"
                            # 캐시에도 담는다. 안 그러면 캐시 HIT 응답만 출처가 사라져
                            # 같은 질문인데 표시가 달라진다. tool start/end 센티넬은
                            # 캐시에 넣지 않는다 — 재생 시 "검색 중"이 헛깜빡인다.
                            buffer.append(line)
                            yield line
                        yield "\x1ftool:end\n"

                    elif kind == "on_chat_model_stream":
                        chunk = event["data"]["chunk"]
                        if (
                            isinstance(chunk, AIMessageChunk)
                            and isinstance(chunk.content, str)
                            and chunk.content
                        ):
                            buffer.append(chunk.content)
                            yield chunk.content

        except asyncio.TimeoutError:
            log.warning("chat.timeout", elapsed_s=time.monotonic() - started)
            yield _error_sentinel(
                "TIMEOUT",
                "응답 시간이 초과되었습니다(120초). 질문을 더 간단히 하거나 잠시 후 다시 시도해주세요.",
            )
        except Exception as e:
            log.exception("chat.unhandled_error", error_type=type(e).__name__)
            yield _error_sentinel(
                "INTERNAL",
                "서버 내부 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
            )
        else:
            if session_was_new and buffer:
                _response_cache[cache_key] = "".join(buffer)
            log.info(
                "chat.done",
                elapsed_s=round(time.monotonic() - started, 2),
                bytes=sum(len(b) for b in buffer),
                tool_calls=tool_calls,
            )

    return StreamingResponse(
        generate(),
        media_type="text/plain; charset=utf-8",
        headers={
            "X-Cache": "MISS",
            "X-Request-Id": request_id,
            "X-Session-Id": session_id,
        },
    )

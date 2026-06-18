import asyncio
import hashlib
import os
import time
import uuid

from cachetools import TTLCache
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessageChunk, HumanMessage
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from agent import agent
from logging_config import configure_logging, get_logger, request_id_var, session_id_var

configure_logging()
log = get_logger("server")

REQUEST_TIMEOUT_S = 120

app = FastAPI()

_cors_origins = os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

_response_cache: TTLCache = TTLCache(maxsize=256, ttl=3600)


class QuestionRequest(BaseModel):
    question: str
    session_id: str | None = None


def _error_sentinel(code: str, message: str) -> str:
    """Frontend가 파싱하는 에러 센티넬. code|message 형태."""
    return f"\x1ferror:{code}|{message}\n"


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/chat")
@limiter.limit("10/minute")
async def chat(request: Request, req: QuestionRequest):
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
        client=get_remote_address(request),
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
                async for event in agent.astream_events(
                    {"messages": [HumanMessage(content=req.question)]},
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
                        log.info("chat.tool_end", tool=event.get("name", "unknown"))
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

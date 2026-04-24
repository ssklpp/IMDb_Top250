import asyncio
import hashlib
import os
import uuid
from cachetools import TTLCache
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from langchain_core.messages import HumanMessage, AIMessageChunk
from agent import agent

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

# 응답 캐시: 최대 256개, TTL 1시간
_response_cache: TTLCache = TTLCache(maxsize=256, ttl=3600)


class QuestionRequest(BaseModel):
    question: str
    session_id: str | None = None


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/chat")
@limiter.limit("10/minute")
async def chat(request: Request, req: QuestionRequest):
    session_was_new = req.session_id is None
    session_id = req.session_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": session_id}}
    cache_key = hashlib.sha256(req.question.strip().lower().encode()).hexdigest()

    # 캐시 히트: 새 세션(대화 컨텍스트 없음)인 경우에만 사용
    if session_was_new and cache_key in _response_cache:
        cached = _response_cache[cache_key]

        async def stream_cached():
            yield cached

        return StreamingResponse(
            stream_cached(),
            media_type="text/plain; charset=utf-8",
            headers={"X-Cache": "HIT"},
        )

    async def generate():
        buffer = []
        try:
            async with asyncio.timeout(120):
                async for event in agent.astream_events(
                    {"messages": [HumanMessage(content=req.question)]},
                    config=config,
                    version="v2",
                ):
                    kind = event["event"]

                    if kind == "on_tool_start":
                        tool_name = event.get("name", "unknown")
                        yield f"\x1ftool:{tool_name}\n"

                    elif kind == "on_tool_end":
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
            yield "\x1ferror:응답 시간이 초과되었습니다. 잠시 후 다시 시도해주세요.\n"
        except Exception:
            yield "\x1ferror:오류가 발생했습니다. 잠시 후 다시 시도해주세요.\n"
        else:
            # 정상 완료 시에만 캐시 저장 (새 세션이고 응답이 있을 때)
            if session_was_new and buffer:
                _response_cache[cache_key] = "".join(buffer)

    return StreamingResponse(
        generate(),
        media_type="text/plain; charset=utf-8",
        headers={"X-Cache": "MISS"},
    )

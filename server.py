import asyncio
import uuid
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from langchain_core.messages import HumanMessage, AIMessageChunk
from agent import agent

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class QuestionRequest(BaseModel):
    question: str
    session_id: str | None = None


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/chat")
async def chat(req: QuestionRequest):
    session_id = req.session_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": session_id}}

    async def generate():
        try:
            async with asyncio.timeout(120):
                async for event in agent.astream_events(
                    {"messages": [HumanMessage(content=req.question)]},
                    config=config,
                    version="v2",
                ):
                    if event["event"] == "on_chat_model_stream":
                        chunk = event["data"]["chunk"]
                        if (
                            isinstance(chunk, AIMessageChunk)
                            and isinstance(chunk.content, str)
                            and chunk.content
                        ):
                            yield chunk.content
        except asyncio.TimeoutError:
            yield "\n[응답 시간이 초과되었습니다.]"
        except Exception as e:
            yield f"\n[오류가 발생했습니다: {str(e)}]"

    return StreamingResponse(generate(), media_type="text/plain; charset=utf-8")

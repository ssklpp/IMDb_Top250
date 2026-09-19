"""질문 하나를 실제 에이전트로 실행하며 도구 호출 인자·결과와 LLM 호출 토큰을 순서대로 보여준다.

"왜 이렇게 답했지?"를 진단할 때 먼저 돌리는 도구다. 답변만 보면 원인을 알 수 없다.
실제로 "지난 주 박스오피스"가 1년 전 데이터를 답한 원인은 날짜를 몰라서가 아니라
데이터가 없을 때 연도를 낮춰 재호출해서였는데, 그건 도구 호출 인자를 찍어 보고서야 알았다.

사용법 (저장소 루트에서, 가상환경 파이썬으로):
    python scripts/trace.py "지난 주 박스오피스 알려줘"
    python scripts/trace.py "어제 박스오피스는?" "기생충 감독은?"   # 여러 질문
    python scripts/trace.py --date 2026-09-14 "지난 주 박스오피스"   # 오늘 날짜를 바꿔 재현
    python scripts/trace.py --full "..."                             # 도구 결과를 자르지 않음

주의: 실제 OpenAI·KOBIS·Tavily를 호출한다. 질문 하나에 보통 1~2센트.
서버와 같은 입력(오늘 날짜 SystemMessage + 질문)을 쓰고, 대화 DB는 건드리지 않는다(MemorySaver).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import uuid
from datetime import date
from pathlib import Path

# agent.py가 import 시점에 로거를 설정하므로 그 전에 로그 소음을 줄인다.
os.environ.setdefault("LOG_LEVEL", "WARNING")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

import agent as agent_module  # noqa: E402
from kobis_format import format_date_context, today_kst  # noqa: E402

PREVIEW_CHARS = 160


def one_line(text: str, limit: int | None) -> str:
    text = " ".join(str(text).split())
    return text if limit is None or len(text) <= limit else text[:limit] + " …"


async def trace(agent, question: str, today: date, full: bool) -> None:
    limit = None if full else PREVIEW_CHARS
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    messages = [SystemMessage(content=format_date_context(today)), HumanMessage(content=question)]

    print("=" * 72)
    print(f"Q: {question}")
    print(f"   (오늘 = {today.isoformat()})")

    answer: list[str] = []
    llm_calls = tool_calls = 0
    tokens_in = tokens_out = 0
    started = time.monotonic()

    async for ev in agent.astream_events({"messages": messages}, config=config, version="v2"):
        kind = ev["event"]
        if kind == "on_chat_model_end":
            llm_calls += 1
            usage = getattr(ev["data"].get("output"), "usage_metadata", None) or {}
            tokens_in += usage.get("input_tokens", 0)
            tokens_out += usage.get("output_tokens", 0)
            print(f"  [LLM {llm_calls}] 입력 {usage.get('input_tokens', '?')} / 출력 {usage.get('output_tokens', '?')} 토큰")
        elif kind == "on_tool_start":
            tool_calls += 1
            print(f"  [도구 {tool_calls}] {ev.get('name')} {ev['data'].get('input')}")
        elif kind == "on_tool_end":
            out = ev["data"].get("output")
            print(f"           → {one_line(getattr(out, 'content', out), limit)}")
        elif kind == "on_chat_model_stream":
            content = getattr(ev["data"]["chunk"], "content", "")
            if isinstance(content, str):
                answer.append(content)

    elapsed = time.monotonic() - started
    print(f"\n  답변: {one_line(''.join(answer), None if full else 400)}")
    print(f"\n  요약: LLM {llm_calls}회 · 도구 {tool_calls}회 · 입력 {tokens_in} / 출력 {tokens_out} 토큰 · {elapsed:.1f}초")


async def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="에이전트 실행 과정을 추적한다")
    parser.add_argument("questions", nargs="+", help="질문 (여러 개 가능)")
    parser.add_argument("--date", help="오늘 날짜를 바꿔서 재현 (YYYY-MM-DD). 날짜 관련 버그 재현용")
    parser.add_argument("--full", action="store_true", help="도구 결과와 답변을 자르지 않음")
    args = parser.parse_args()

    today = date.fromisoformat(args.date) if args.date else today_kst()
    if args.date:
        # 프롬프트의 날짜뿐 아니라 도구의 "집계 전 날짜 차단"도 같은 날짜를 보게 한다.
        # agent.py는 today_kst를 이름으로 가져와 호출 시점에 찾으므로 모듈 속성을 바꾸면 된다.
        agent_module.today_kst = lambda: today

    agent = agent_module.build_agent()
    for q in args.questions:
        await trace(agent, q, today, args.full)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

"""골든 데이터셋에 대한 회귀 평가 러너.

각 항목에 대해:
1. 에이전트를 호출하고 사용된 도구를 추적
2. 기대 도구 호출 여부 검증
3. 기대 키워드 포함 여부 검증
4. (옵션) LLM-as-judge로 rubric 기반 점수

사용법:
    python -m tests.evals.run_evals                  # 전체 실행
    python -m tests.evals.run_evals --skip-judge     # LLM-as-judge 생략 (빠름, API 비용 0)
    python -m tests.evals.run_evals --ids imdb-001   # 특정 ID만
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가하여 agent 모듈 import 가능하게 함
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from langchain_core.messages import HumanMessage  # noqa: E402
from langchain_openai import ChatOpenAI  # noqa: E402

from agent import agent  # noqa: E402

DATASET_PATH = Path(__file__).parent / "golden_dataset.json"


@dataclass
class EvalResult:
    id: str
    question: str
    answer: str
    tools_called: list[str]
    expected_tools: list[str]
    expected_keywords: list[str]
    tools_ok: bool
    keywords_ok: bool
    judge_score: int | None = None
    judge_reason: str = ""
    elapsed_s: float = 0.0
    error: str | None = None

    @property
    def passed(self) -> bool:
        if self.error:
            return False
        # judge 점수가 있으면 4 이상이어야 통과
        judge_ok = self.judge_score is None or self.judge_score >= 4
        return self.tools_ok and self.keywords_ok and judge_ok


JUDGE_PROMPT = """당신은 AI 챗봇 응답을 평가하는 엄격한 채점관입니다.

[질문]
{question}

[기대하는 답변 기준 (Rubric)]
{rubric}

[챗봇이 실제 답변한 내용]
{answer}

위 답변이 rubric을 얼마나 잘 충족하는지 1~5점으로 평가하세요:
- 5: rubric을 완벽히 충족
- 4: 주요 요구를 충족하지만 사소한 누락
- 3: 부분적으로만 충족
- 2: 거의 충족 못함
- 1: 완전히 실패 또는 무관한 답변

JSON 형식으로만 답변하세요:
{{"score": <1-5>, "reason": "<한 문장 이유>"}}"""


async def run_one(item: dict, judge_llm: ChatOpenAI | None) -> EvalResult:
    question = item["question"]
    expected_tools = item.get("expected_tools", [])
    expected_keywords = item.get("expected_keywords", [])
    any_match = item.get("expected_keywords_any", True)
    rubric = item.get("rubric", "")

    tools_called: list[str] = []
    buffer: list[str] = []
    started = time.monotonic()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    try:
        async for event in agent.astream_events(
            {"messages": [HumanMessage(content=question)]},
            config=config,
            version="v2",
        ):
            kind = event["event"]
            if kind == "on_tool_start":
                tools_called.append(event.get("name", "unknown"))
            elif kind == "on_chat_model_stream":
                chunk = event["data"]["chunk"]
                content = getattr(chunk, "content", None)
                if isinstance(content, str):
                    buffer.append(content)
    except Exception as e:
        return EvalResult(
            id=item["id"],
            question=question,
            answer="",
            tools_called=tools_called,
            expected_tools=expected_tools,
            expected_keywords=expected_keywords,
            tools_ok=False,
            keywords_ok=False,
            elapsed_s=time.monotonic() - started,
            error=f"{type(e).__name__}: {e}",
        )

    answer = "".join(buffer).strip()
    elapsed = time.monotonic() - started

    if not expected_tools:
        tools_ok = True
    else:
        tools_ok = any(t in tools_called for t in expected_tools)

    if not expected_keywords:
        keywords_ok = True
    elif any_match:
        keywords_ok = any(kw.lower() in answer.lower() for kw in expected_keywords)
    else:
        keywords_ok = all(kw.lower() in answer.lower() for kw in expected_keywords)

    judge_score: int | None = None
    judge_reason = ""
    if judge_llm is not None and rubric and answer:
        try:
            judge_resp = await judge_llm.ainvoke(
                JUDGE_PROMPT.format(question=question, rubric=rubric, answer=answer)
            )
            judge_text = judge_resp.content if hasattr(judge_resp, "content") else str(judge_resp)
            data = json.loads(judge_text)
            judge_score = int(data.get("score", 0))
            judge_reason = str(data.get("reason", ""))
        except Exception as e:
            judge_reason = f"(judge failed: {e})"

    return EvalResult(
        id=item["id"],
        question=question,
        answer=answer,
        tools_called=tools_called,
        expected_tools=expected_tools,
        expected_keywords=expected_keywords,
        tools_ok=tools_ok,
        keywords_ok=keywords_ok,
        judge_score=judge_score,
        judge_reason=judge_reason,
        elapsed_s=elapsed,
    )


def print_result(r: EvalResult) -> None:
    status = "✓ PASS" if r.passed else "✗ FAIL"
    print(f"\n[{status}] {r.id}  ({r.elapsed_s:.1f}s)")
    print(f"  Q: {r.question}")
    if r.error:
        print(f"  ERROR: {r.error}")
        return
    print(f"  tools: called={r.tools_called} expected={r.expected_tools} → {'OK' if r.tools_ok else 'MISS'}")
    print(f"  keywords: {r.expected_keywords} → {'OK' if r.keywords_ok else 'MISS'}")
    if r.judge_score is not None:
        print(f"  judge: {r.judge_score}/5 — {r.judge_reason}")
    snippet = r.answer.replace("\n", " ")[:150]
    print(f"  answer: {snippet}{'...' if len(r.answer) > 150 else ''}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-judge", action="store_true", help="LLM-as-judge 생략")
    parser.add_argument("--ids", nargs="*", help="특정 ID만 실행")
    parser.add_argument("--judge-model", default="gpt-5.4-mini", help="judge용 모델")
    args = parser.parse_args()

    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    if args.ids:
        dataset = [item for item in dataset if item["id"] in args.ids]
        if not dataset:
            print(f"ID 매칭 없음: {args.ids}")
            return 1

    judge_llm: ChatOpenAI | None = None
    if not args.skip_judge:
        if not os.environ.get("OPENAI_API_KEY"):
            print("⚠ OPENAI_API_KEY 없음 — judge 자동 비활성화")
        else:
            judge_llm = ChatOpenAI(model_name=args.judge_model, temperature=0)

    print(f"평가 시작: {len(dataset)}개 항목 (judge={'on' if judge_llm else 'off'})")

    results: list[EvalResult] = []
    for item in dataset:
        result = await run_one(item, judge_llm)
        print_result(result)
        results.append(result)

    passed = sum(1 for r in results if r.passed)
    total = len(results)
    judge_scores = [r.judge_score for r in results if r.judge_score is not None]
    avg_judge = sum(judge_scores) / len(judge_scores) if judge_scores else None

    print("\n" + "=" * 60)
    print(f"결과: {passed}/{total} 통과 ({100*passed/total:.0f}%)")
    if avg_judge is not None:
        print(f"평균 judge 점수: {avg_judge:.2f}/5")
    print("=" * 60)

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

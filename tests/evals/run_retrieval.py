"""검색 적중률 — imdb_search의 검색 단계만 따로 잰다.

골든셋 평가는 최종 답변만 본다. 모델이 사전지식으로 맞혀도 통과해서, 잘린 PDF 시절
정답이 검색 자료에 아예 없었는데도 15/15가 나왔다. corpus_evidence 검사는 "정답이
자료에 있는가"까지만 본다. 이 스크립트는 그 다음 칸 — "검색이 정답을 실제로 가져오는가".

두 가지 기준으로 잰다.
- 원문 기준(기본): 사용자 질문을 그대로 검색어로 넣는다. LLM 없이 임베딩만(1센트 미만).
  검색 자체의 실력이다.
- 실제 기준(--actual): 에이전트를 실제로 돌려 모델이 imdb_search에 넘긴 검색어와 그때
  가져온 결과를 쓴다. 서비스에서 실제로 일어나는 일이다. LLM을 부르므로 과금된다(약 50센트).
  모델은 검색어를 영어로 바꿔 넘기기도 하고, 검색 대신 다른 도구를 고르기도 한다.

사용법 (저장소 루트에서):
    python -m tests.evals.run_retrieval
    python -m tests.evals.run_retrieval --all       # 질문마다 결과를 전부 출력
    python -m tests.evals.run_retrieval --actual    # 원문 기준과 실제 기준을 나란히
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import uuid
from collections import Counter, defaultdict
from pathlib import Path

os.environ.setdefault("LOG_LEVEL", "WARNING")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

from agent import build_agent, retriever  # noqa: E402
from kobis_format import format_date_context, today_kst  # noqa: E402

SET_PATH = Path(__file__).parent / "retrieval_set.json"
KS = (1, 3, 8)
CATEGORY_LABEL = {
    "en_title": "영어 제목",
    "ko_title": "한국어 제목",
    "original_title": "원제로 저장된 영화",
    "person": "인명",
    "plot": "줄거리 묘사",
}
HANGUL = re.compile(r"[가-힣]")
CONCURRENCY = 5


def first_hit(titles: list[str], gold: list[str]) -> int | None:
    """정답 중 하나가 처음 나온 순위(1부터). 없으면 None."""
    for i, t in enumerate(titles, 1):
        if t in gold:
            return i
    return None


def scores(positions: list[int | None]) -> list[float]:
    """[Hit@1, Hit@3, Hit@8, MRR]. 빈 목록이면 빈 리스트."""
    n = len(positions)
    if not n:
        return []
    hits = [sum(1 for p in positions if p is not None and p <= k) / n for k in KS]
    return hits + [sum(1 / p for p in positions if p is not None) / n]


def fmt(vals: list[float]) -> str:
    return "  ".join(f"{v:5.2f}" for v in vals) if vals else "    -"


def raw_positions(items: list[dict]) -> dict[str, tuple[int | None, list[str]]]:
    out = {}
    for it in items:
        titles = [d.metadata["title"] for d in retriever.invoke(it["query"])]
        out[it["id"]] = (first_hit(titles, it["gold"]), titles)
    return out


async def actual_calls(items: list[dict]) -> dict[str, list[tuple[str, str, list[str] | None]]]:
    """질문마다 [(도구 이름, 검색어, imdb_search면 가져온 제목들)]. 서버와 같은 입력을 쓴다."""
    agent = build_agent()
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(it):
        async with sem:
            inputs, calls = {}, []
            messages = [SystemMessage(content=format_date_context(today_kst())), HumanMessage(content=it["query"])]
            config = {"configurable": {"thread_id": str(uuid.uuid4())}}
            async for ev in agent.astream_events({"messages": messages}, config=config, version="v2"):
                if ev["event"] == "on_tool_start":
                    inputs[ev["run_id"]] = ev["data"].get("input") or {}
                elif ev["event"] == "on_tool_end":
                    arg = inputs.get(ev["run_id"], {})
                    query = arg.get("query", "") if isinstance(arg, dict) else str(arg)
                    artifact = getattr(ev["data"].get("output"), "artifact", None)
                    titles = [d.metadata["title"] for d in artifact] if ev["name"] == "imdb_search" and isinstance(artifact, list) else None
                    calls.append((ev["name"], query, titles))
            return it["id"], calls

    return dict(await asyncio.gather(*(one(it) for it in items)))


def report_raw(items, raw, show_all):
    header = "".join(f"Hit@{x:<3}" for x in KS) + "  MRR"
    print(f"검색 적중률 — 원문 기준, 질문 {len(items)}개, 검색 {retriever.search_kwargs['k']}편\n")
    print(f"{'구분':<22}{'개수':>4}   {header}")

    def row(label, group):
        print(f"{label:<22}{len(group):>4}   {fmt(scores([raw[it['id']][0] for it in group]))}")

    row("전체", items)
    print()
    for cat, label in CATEGORY_LABEL.items():
        group = [it for it in items if it["category"] == cat]
        if group:
            row(label, group)
    print()
    row("영어 질문", [it for it in items if not HANGUL.search(it["query"])])
    row("한국어 질문", [it for it in items if HANGUL.search(it["query"])])

    shown = items if show_all else [it for it in items if raw[it["id"]][0] is None or raw[it["id"]][0] > 3]
    if shown:
        print("\n" + ("질문별 결과" if show_all else f"상위 3편 밖 ({len(shown)}개)"))
        for it in shown:
            pos, titles = raw[it["id"]]
            print(f"  {it['id']:<10} {(f'{pos}번째' if pos else '없음'):>5}  {it['query']}")
            print(f"  {'':<10} {'':>5}  → {', '.join(titles[:3])}")


def report_actual(items, raw, actual, show_all):
    # 실제 기준 순위: imdb_search를 여러 번 불렀으면 가장 좋은 결과(모델은 전부 본다)
    best = {}
    for it in items:
        found = [first_hit(t, it["gold"]) for name, _, t in actual[it["id"]] if name == "imdb_search" and t is not None]
        best[it["id"]] = min((p for p in found if p is not None), default=None) if found else "no_search"
    searched = [it for it in items if best[it["id"]] != "no_search"]

    print(f"검색 적중률 — 원문 vs 실제 기준, 질문 {len(items)}개\n")
    tools = Counter()
    for it in items:
        names = {n for n, _, _ in actual[it["id"]]}
        tools["imdb_search 호출" if "imdb_search" in names else (" + ".join(sorted(names)) + "만" if names else "도구 없음")] += 1
    print("모델의 도구 선택")
    for k, v in tools.most_common():
        print(f"  {k:<34}{v:>3}개")

    ko_queries = [(it, q) for it in searched for n, q, _ in actual[it["id"]] if n == "imdb_search"]
    ko_count = sum(1 for _, q in ko_queries if HANGUL.search(q))
    print(f"\nimdb_search 호출 {len(ko_queries)}번 중 한국어가 섞인 검색어 {ko_count}번")

    print(f"\n{'구분':<20}{'검색함':>6}   {'원문 Hit@8':>10} {'실제 Hit@8':>10}   {'원문 MRR':>8} {'실제 MRR':>8}")

    def row(label, group):
        g = [it for it in group if best[it["id"]] != "no_search"]
        r = scores([raw[it["id"]][0] for it in g])
        a = scores([best[it["id"]] for it in g])
        cell = lambda v, i: f"{v[i]:.2f}" if v else "-"
        print(f"{label:<20}{len(g):>3}/{len(group):<3}  {cell(r, 2):>10} {cell(a, 2):>10}   {cell(r, 3):>8} {cell(a, 3):>8}")

    row("전체", items)
    print()
    for cat, label in CATEGORY_LABEL.items():
        group = [it for it in items if it["category"] == cat]
        if group:
            row(label, group)
    print()
    row("영어 질문", [it for it in items if not HANGUL.search(it["query"])])
    row("한국어 질문", [it for it in items if HANGUL.search(it["query"])])
    print("\n※ 두 기준 모두 imdb_search를 부른 질문만으로 계산한다(같은 질문 집합끼리 비교).")

    def interesting(it):
        b = best[it["id"]]
        return show_all or b == "no_search" or b is None or b > 3

    shown = [it for it in items if interesting(it)]
    if shown:
        print("\n" + ("질문별 결과" if show_all else f"검색 안 함 · 상위 3편 밖 ({len(shown)}개)"))
        for it in shown:
            b = best[it["id"]]
            where = "검색 안 함" if b == "no_search" else (f"{b}번째" if b else "없음")
            print(f"  {it['id']:<10} {where:>7}  {it['query']}")
            for name, q, titles in actual[it["id"]]:
                tail = f" → {', '.join(titles[:3])}" if titles else ""
                print(f"  {'':<10} {'':>7}    {name}({q!r}){tail}")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="imdb_search 검색 적중률")
    parser.add_argument("--all", action="store_true", help="질문마다 결과를 출력")
    parser.add_argument("--actual", action="store_true", help="에이전트를 실제로 돌려 모델의 검색어로도 잰다 (과금)")
    args = parser.parse_args()

    items = json.loads(SET_PATH.read_text(encoding="utf-8"))
    raw = raw_positions(items)
    if args.actual:
        report_actual(items, raw, asyncio.run(actual_calls(items)), args.all)
    else:
        report_raw(items, raw, args.all)
    return 0


if __name__ == "__main__":
    sys.exit(main())

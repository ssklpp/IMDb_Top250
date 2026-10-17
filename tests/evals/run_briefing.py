"""브리핑 초안 평가 — 실제 LLM이 저장해 둔 KOBIS 응답(tests/fixtures)으로 초안을 쓰고 검사를 통과하는지 잰다.

    python -m tests.evals.run_briefing            # 10번
    python -m tests.evals.run_briefing --n 20 --show

LLM을 부르므로 과금된다(작은 모델 10~20번). KOBIS는 부르지 않는다.
그래프와 같은 규칙: 첫 초안이 검사에 걸리면 문제를 알려 주고 한 번 더 쓴다. 두 번 다 걸리면 실패.

지표
- 첫 시도 통과율: 모델이 규칙(숫자 금지·제목 그대로·길이)을 처음부터 지키는 비율
- 최종 통과율: 다시 쓰기까지 포함해 승인 대기로 갈 수 있는 비율. 실패는 그 주 브리핑이 안 나간다는 뜻
- 걸린 이유: 어떤 규칙을 자주 어기는지(프롬프트를 고칠 곳)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent import llm  # noqa: E402
from briefing.report import compare, render, validate_draft  # noqa: E402
from briefing.writer import write_draft  # noqa: E402
from kobis_format import parse_weekly  # noqa: E402

FIX = ROOT / "tests" / "fixtures"


def load(d: str) -> dict:
    return parse_weekly(json.loads((FIX / f"kobis_weekly_{d}.json").read_text(encoding="utf-8")))


async def one(report: dict) -> tuple[bool, bool, list[str], str]:
    first = await write_draft(llm, report)
    problems = validate_draft(first, report)
    if not problems:
        return True, True, [], render(report, first)
    second = await write_draft(llm, report, problems)
    problems2 = validate_draft(second, report)
    return False, not problems2, problems, render(report, second) if not problems2 else ""


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=10)
    parser.add_argument("--show", action="store_true", help="통과한 발송문 하나를 출력")
    args = parser.parse_args()

    report = compare(load("20261011"), load("20261004"))
    results = await asyncio.gather(*(one(report) for _ in range(args.n)))

    first_ok = sum(r[0] for r in results)
    final_ok = sum(r[1] for r in results)
    reasons = Counter(p.split(":")[0].split("에 ")[-1] if "숫자" in p else p.split(" ")[0] for r in results for p in r[2])
    print(f"브리핑 초안 평가 — {args.n}번")
    print(f"  첫 시도 통과: {first_ok}/{args.n}")
    print(f"  최종 통과(다시 쓰기 포함): {final_ok}/{args.n}")
    if reasons:
        print("  첫 시도에서 걸린 이유:")
        for k, v in reasons.most_common():
            print(f"    {v}회  {k}")
    if args.show:
        sample = next((r[3] for r in results if r[3]), "")
        print("\n--- 발송문 예시 ---\n" + sample)
    return 0 if final_ok == args.n else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

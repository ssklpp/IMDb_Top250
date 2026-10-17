"""브리핑 승인 흐름을 **실제 Postgres**로 검사한다. TEST_DATABASE_URL이 없으면 건너뛴다.

    docker run -d --rm --name brief-pg -e POSTGRES_PASSWORD=test -p 55432:5432 postgres:16
    TEST_DATABASE_URL=postgresql://postgres:test@localhost:55432/postgres pytest tests/integration -q

CI에서는 deps 잡이 Postgres 서비스 컨테이너를 띄워 돌린다. 외부 API·LLM은 부르지 않는다(초안·발송은 가짜).

보강 계획 3단계의 완료 기준을 그대로 옮겼다:
- 승인 대기 중에 서버가 재시작돼도, 승인하면 정상 발송된다
- 같은 승인을 두 번 보내도 한 번만 발송된다
- 거절·만료된 브리핑은 발송되지 않고 기록만 남는다
"""

import asyncio
import os
import sys
import uuid
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402
from psycopg_pool import AsyncConnectionPool  # noqa: E402

from briefing.graph import BriefingService, DecisionConflict, build_graph  # noqa: E402
from briefing.store import BriefingStore  # noqa: E402

DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB, reason="TEST_DATABASE_URL이 없으면 건너뛴다")

WEEK = (date(2026, 10, 12), date(2026, 10, 18))


def run(coro):
    # psycopg의 async는 Windows 기본 이벤트 루프(Proactor)에서 동작하지 않는다. 서버는 리눅스 컨테이너라 상관없다.
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    return asyncio.run(coro, loop_factory=factory)


class FakeSender:
    def __init__(self):
        self.sent: list[str] = []

    async def send(self, text: str) -> str:
        await asyncio.sleep(0.05)  # 동시 승인 경쟁이 실제로 겹치도록 발송을 조금 늦춘다
        self.sent.append(text)
        return f"fake-{len(self.sent)}"


class App:
    """서버 한 프로세스에 해당하는 묶음. 재시작은 닫고 새로 만드는 것으로 흉내 낸다."""

    def __init__(self, sender, draft="1위. 테스트 영화 — 관객수: 1,000명", ttl=48):
        self.sender, self.draft_text, self.ttl, self.draft_calls = sender, draft, ttl, 0

    async def __aenter__(self):
        self.pool = AsyncConnectionPool(
            DB, open=False, kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row}
        )
        await self.pool.open(wait=True)
        saver = AsyncPostgresSaver(self.pool)
        await saver.setup()
        self.store = BriefingStore(self.pool)
        await self.store.setup()

        async def draft_fn(week_start, week_end):
            self.draft_calls += 1
            return self.draft_text

        graph = build_graph(saver, self.store, draft_fn, self.sender)
        self.service = BriefingService(graph, self.store, self.ttl)
        return self

    async def __aexit__(self, *exc):
        await self.pool.close()


def new_id():
    return f"briefing-test-{uuid.uuid4().hex[:10]}"


def test_트리거를_두_번_보내도_브리핑은_하나():
    """트리거(n8n·cron)가 재시도해도 같은 주 브리핑이 두 번 만들어지면 안 된다."""
    async def go():
        tid = new_id()
        async with App(FakeSender()) as app:
            first, created1 = await app.service.run(tid, *WEEK)
            second, created2 = await app.service.run(tid, *WEEK)
            return first, created1, second, created2, app.draft_calls

    first, created1, second, created2, draft_calls = run(go())
    assert (created1, created2) == (True, False)
    assert first["status"] == second["status"] == "pending"
    assert draft_calls == 1


def test_승인_대기_중_재시작해도_승인하면_발송된다():
    """그래프 상태가 프로세스 밖(Postgres)에 있어야 트리거한 프로세스와 승인받는 프로세스가 달라도 된다."""
    sender = FakeSender()

    async def go():
        tid = new_id()
        async with App(sender) as app:  # 첫 번째 프로세스: 초안까지 만들고 멈춘다
            await app.service.run(tid, *WEEK)
        async with App(sender) as app:  # 재시작된 프로세스: 같은 thread_id로 재개
            row = await app.service.decide(tid, "approve", None, "tester")
        return row

    row = run(go())
    assert row["status"] == "sent"
    assert row["delivery_id"] == "fake-1"
    assert sender.sent == ["1위. 테스트 영화 — 관객수: 1,000명"]


def test_승인을_동시에_두_번_보내도_한_번만_발송():
    """버튼을 두 번 누르거나 재시도가 겹쳐도 웹훅은 한 번만 나가야 한다. 웹훅에는 중복 방지 키가 없다."""
    sender = FakeSender()

    async def go():
        tid = new_id()
        async with App(sender) as app:
            await app.service.run(tid, *WEEK)
            results = await asyncio.gather(
                app.service.decide(tid, "approve", None, "a"),
                app.service.decide(tid, "approve", None, "b"),
                return_exceptions=True,
            )
            return results, await app.store.get(tid)

    results, row = run(go())
    assert row["status"] == "sent"
    assert len(sender.sent) == 1
    assert sum(isinstance(r, dict) for r in results) >= 1
    assert all(isinstance(r, (dict, DecisionConflict)) for r in results), results


def test_이미_발송된_브리핑에_다시_승인하면_409():
    sender = FakeSender()

    async def go():
        tid = new_id()
        async with App(sender) as app:
            await app.service.run(tid, *WEEK)
            await app.service.decide(tid, "approve", None, "a")
            try:
                await app.service.decide(tid, "approve", None, "a")
            except DecisionConflict as e:
                return str(e)

    assert run(go()) == "sent"
    assert len(sender.sent) == 1


def test_수정해서_승인하면_수정본이_나간다():
    sender = FakeSender()

    async def go():
        tid = new_id()
        async with App(sender) as app:
            await app.service.run(tid, *WEEK)
            return await app.service.decide(tid, "edit", "고친 본문", "editor")

    row = run(go())
    assert row["status"] == "sent" and row["final_text"] == "고친 본문"
    assert sender.sent == ["고친 본문"]


def test_거절하면_발송하지_않고_기록만_남는다():
    sender = FakeSender()

    async def go():
        tid = new_id()
        async with App(sender) as app:
            await app.service.run(tid, *WEEK)
            return await app.service.decide(tid, "reject", None, "reviewer")

    row = run(go())
    assert row["status"] == "rejected" and row["decided_by"] == "reviewer"
    assert sender.sent == []


def test_만료되면_승인해도_발송되지_않는다():
    """승인이 너무 늦으면(지난 주 브리핑을 며칠 뒤 승인) 보내지 않는다."""
    sender = FakeSender()

    async def go():
        tid = new_id()
        async with App(sender, ttl=1) as app:
            await app.service.run(tid, *WEEK)
            async with app.pool.connection() as conn:  # 승인 대기가 2시간 전에 시작된 것으로 만든다
                await conn.execute(
                    "UPDATE briefing_runs SET updated_at = now() - interval '2 hours' WHERE thread_id = %s", (tid,)
                )
            try:
                await app.service.decide(tid, "approve", None, "late")
            except DecisionConflict as e:
                return str(e), await app.store.get(tid)

    status, row = run(go())
    assert status == "expired" and row["status"] == "expired"
    assert sender.sent == []


def test_초안을_못_만들면_승인을_요청하지_않는다():
    """KOBIS가 실패하면(집계 전·장애) 빈 브리핑을 승인 대기에 올리지 않는다."""
    async def go():
        tid = new_id()
        async with App(FakeSender(), draft="[TOOL_ERROR code=NO_DATA] 아직 집계 전") as app:
            row, _ = await app.service.run(tid, *WEEK)
            return row

    row = run(go())
    assert row["status"] == "failed" and row["error"].startswith("[TOOL_ERROR code=NO_DATA]")


def test_결정_기록_후_재개_전에_죽었다면_재시도로_발송된다():
    """승인 API가 결정을 기록하고 그래프를 재개하기 전에 프로세스가 죽은 경우. 같은 승인을 다시 보내면
    재개만 하고 발송은 한 번이다."""
    sender = FakeSender()

    async def go():
        tid = new_id()
        async with App(sender) as app:
            await app.service.run(tid, *WEEK)
            await app.store.decide(tid, True, "1위. 테스트 영화 — 관객수: 1,000명", "a")  # 기록만 하고 '죽음'
        async with App(sender) as app:
            return await app.service.decide(tid, "approve", None, "a")

    row = run(go())
    assert row["status"] == "sent"
    assert len(sender.sent) == 1


def test_그래프가_두_번_재개돼도_발송은_한_번():
    """위 상황에서 재시도 두 개가 동시에 오면 그래프가 두 번 재개돼 deliver가 두 번 돈다.
    claim_send(approved → sending 조건부 UPDATE)가 유일한 방어선이다 — 이 조건을 빼면 두 번 발송된다."""
    sender = FakeSender()

    async def go():
        tid = new_id()
        async with App(sender) as app:
            await app.service.run(tid, *WEEK)
            await app.store.decide(tid, True, "본문", "a")
            await asyncio.gather(
                app.service.decide(tid, "approve", None, "a"),
                app.service.decide(tid, "approve", None, "a"),
                return_exceptions=True,
            )
            return await app.store.get(tid)

    row = run(go())
    assert row["status"] == "sent"
    assert sender.sent == ["본문"]

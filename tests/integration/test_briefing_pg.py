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
import json
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

# 저장해 둔 실제 KOBIS 응답(tests/fixtures)의 주. 전주(20261004)도 있어서 비교까지 돈다.
WEEK = (date(2026, 10, 5), date(2026, 10, 11))
FIX = Path(__file__).resolve().parents[1] / "fixtures"
GOOD_DRAFT = {
    "headline": "암살자(들)이 정상을 지킨 가운데 룩백이 순위권으로 뛰어올랐다",
    "comments": [{"rank": 6, "title": "룩백", "comment": "전주 순위권 밖에서 단숨에 올라온 역주행"}],
    "summary": "상위권은 그대로였고 중하위권에 변화가 컸다.",
}
BAD_DRAFT = {**GOOD_DRAFT, "summary": "암살자(들)이 75만 명을 모았다."}  # 모델 글에 숫자 → 검사에 걸린다


def fixture(target_dt):
    return json.loads((FIX / f"kobis_weekly_{target_dt}.json").read_text(encoding="utf-8"))


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

    def __init__(self, sender, drafts=(GOOD_DRAFT,), fetch=fixture, ttl=48, notifier=None):
        """drafts: 모델이 차례로 돌려줄 초안들(마지막 것을 계속 준다). fetch: YYYYMMDD → KOBIS 원본(실패면 예외)."""
        self.sender, self.drafts, self.fetch, self.ttl, self.notifier = sender, list(drafts), fetch, ttl, notifier
        self.draft_calls, self.feedback = 0, []

    async def __aenter__(self):
        self.pool = AsyncConnectionPool(
            DB, open=False, kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row}
        )
        await self.pool.open(wait=True)
        saver = AsyncPostgresSaver(self.pool)
        await saver.setup()
        self.store = BriefingStore(self.pool)
        await self.store.setup()

        async def fetch_fn(target_dt):
            return self.fetch(target_dt)

        async def write_fn(report, feedback):
            self.feedback.append(feedback)
            d = self.drafts[min(self.draft_calls, len(self.drafts) - 1)]
            self.draft_calls += 1
            return d

        graph = build_graph(saver, self.store, fetch_fn, write_fn, self.sender, self.notifier, "http://admin")
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
    assert len(sender.sent) == 1
    # 발송문 숫자는 원자료 그대로(모델이 쓴 게 아니라 render가 찍은 것)
    assert "1위 암살자(들) — 759,106명 (누적 2,993,844명)" in sender.sent[0]


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


def test_KOBIS가_실패하면_승인을_요청하지_않는다():
    """집계 전·장애로 원자료가 없으면 빈 브리핑을 승인 대기에 올리지 않는다."""
    def broken(target_dt):
        raise ConnectionError("kobis down")

    async def go():
        tid = new_id()
        async with App(FakeSender(), fetch=broken) as app:
            row, _ = await app.service.run(tid, *WEEK)
            return row, app.draft_calls

    row, draft_calls = run(go())
    assert row["status"] == "failed" and "KOBIS 조회 실패" in row["error"]
    assert draft_calls == 0  # 모델을 부르지도 않는다


def test_집계_기간이_대상_주와_다르면_보내지_않는다():
    """'지난 주'에 1년 전 데이터를 답한 적이 있다. KOBIS가 다른 주를 주면 승인 대기에도 올리지 않는다."""
    async def go():
        tid = new_id()
        # 무엇을 물어도 2026-10-05~11 응답을 주는 KOBIS — 대상 주(2025-10-06~12)와 기간이 어긋난다.
        async with App(FakeSender(), fetch=lambda target_dt: fixture("20261011")) as app:
            row, _ = await app.service.run(tid, date(2025, 10, 6), date(2025, 10, 12))
            return row

    row = run(go())
    assert row["status"] == "failed" and "대상 주" in row["error"]


def test_모델이_숫자를_쓰면_한_번_다시_쓰게_한다():
    """첫 초안이 검사에 걸리면 문제를 알려 주고 한 번 더 쓴다. 두 번째가 통과하면 승인 대기."""
    async def go():
        tid = new_id()
        async with App(FakeSender(), drafts=(BAD_DRAFT, GOOD_DRAFT)) as app:
            row, _ = await app.service.run(tid, *WEEK)
            return row, app.feedback

    row, feedback = run(go())
    assert row["status"] == "pending" and row["draft_attempts"] == 2
    assert feedback[0] is None and any("숫자" in f for f in feedback[1])
    assert "75만" not in row["draft"]


def test_두_번_다_검사에_걸리면_발송하지_않는다():
    """원자료와 다른 숫자가 든 초안은 사람이 승인하기 전에 막힌다(완료 기준)."""
    async def go():
        tid = new_id()
        async with App(FakeSender(), drafts=(BAD_DRAFT,)) as app:
            row, _ = await app.service.run(tid, *WEEK)
            return row, app.draft_calls

    row, draft_calls = run(go())
    assert row["status"] == "failed" and "초안 검사 실패" in row["error"]
    assert draft_calls == 2 and row["draft"] is None


def test_실패한_주는_다시_트리거하면_처음부터_다시_돈다():
    """월요일 아침에 KOBIS 집계가 아직이면 실패한다. 오후에 다시 트리거하면 그 주를 다시 만들어야 한다."""
    calls = {"n": 0}

    def flaky(target_dt):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("not yet")
        return fixture(target_dt)

    async def go():
        tid = new_id()
        async with App(FakeSender(), fetch=flaky) as app:
            first, _ = await app.service.run(tid, *WEEK)
            second, created = await app.service.run(tid, *WEEK)
            return first, second, created

    first, second, created = run(go())
    assert first["status"] == "failed"
    assert second["status"] == "pending" and created is False and second["error"] is None


def test_전주를_못_가져와도_이번_주_브리핑은_만든다():
    def no_prev(target_dt):
        if target_dt != "20261011":
            raise ConnectionError("prev missing")
        return fixture(target_dt)

    async def go():
        tid = new_id()
        async with App(FakeSender(), fetch=no_prev) as app:
            row, _ = await app.service.run(tid, *WEEK)
            return row

    row = run(go())
    assert row["status"] == "pending" and "순위권 진입" not in row["draft"]


def test_승인_대기가_되면_검토자에게_한_번_알린다():
    notifier = FakeSender()

    async def go():
        tid = new_id()
        async with App(FakeSender(), notifier=notifier) as app:
            await app.service.run(tid, *WEEK)
            await app.service.decide(tid, "approve", None, "a")  # 재개해도 알림이 다시 가면 안 된다

    run(go())
    assert len(notifier.sent) == 1 and "승인 대기" in notifier.sent[0] and "http://admin" in notifier.sent[0]


def test_결정_기록_후_재개_전에_죽었다면_재시도로_발송된다():
    """승인 API가 결정을 기록하고 그래프를 재개하기 전에 프로세스가 죽은 경우. 같은 승인을 다시 보내면
    재개만 하고 발송은 한 번이다."""
    sender = FakeSender()

    async def go():
        tid = new_id()
        async with App(sender) as app:
            await app.service.run(tid, *WEEK)
            await app.store.decide(tid, True, "발송문", "a")  # 기록만 하고 '죽음'
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


def test_KOBIS가_빈_목록을_주면_초안을_만들지_않는다():
    """집계 전인 주를 물으면 KOBIS는 에러 대신 빈 목록을 준다(실호출로 확인한 동작). 빈 브리핑을 승인 대기에 올리지 않는다."""
    empty = {"boxOfficeResult": {"showRange": "20261005~20261011", "weeklyBoxOfficeList": []}}

    async def go():
        tid = new_id()
        async with App(FakeSender(), fetch=lambda target_dt: empty) as app:
            row, _ = await app.service.run(tid, *WEEK)
            return row, app.draft_calls

    row, draft_calls = run(go())
    assert row["status"] == "failed" and "집계 전" in row["error"]
    assert draft_calls == 0


def test_운영_지표():
    """roadmap 5절 지표가 briefing_runs에서 맞게 계산되는지. 다섯 가지 결과를 하나씩 만들어 본다."""
    async def go():
        async with App(FakeSender()) as app:
            async with app.pool.connection() as conn:
                await conn.execute("DELETE FROM briefing_runs")
        results = {}
        for name, drafts, action in [
            ("unedited", (GOOD_DRAFT,), "approve"),
            ("edited", (GOOD_DRAFT,), "edit"),
            ("rejected", (GOOD_DRAFT,), "reject"),
            ("redrafted", (BAD_DRAFT, GOOD_DRAFT), "approve"),
            ("validate_failed", (BAD_DRAFT,), None),
        ]:
            async with App(FakeSender(), drafts=drafts) as app:
                tid = new_id()
                await app.service.run(tid, *WEEK, trigger="n8n")
                if action:
                    await app.service.decide(tid, action, "고친 본문" if action == "edit" else None, "m")
                results[name] = await app.store.get(tid)
        async with App(FakeSender()) as app:
            return results, await app.service.metrics()

    results, m = run(go())
    assert m["total"] == 5
    assert m["by_status"] == {"sent": 3, "pending": 0, "rejected": 1, "expired": 0, "failed": 1}
    assert m["approved_unedited_ratio"] == round(2 / 3, 3)  # 승인 3건 중 수정 없이 2건
    assert m["redraft_ratio"] == round(2 / 5, 3)  # 다시 쓴 1건 + 두 번 다 걸린 1건 / 초안 단계까지 간 5건
    assert m["validate_failed"] == 1
    assert m["hours_to_decision_avg"] is not None and m["hours_to_decision_avg"] < 1
    assert m["prep_seconds_avg"] is not None
    assert results["unedited"]["trigger"] == "n8n" and results["unedited"]["llm_tokens"] == 0  # 가짜 writer는 LLM을 안 부른다

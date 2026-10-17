"""브리핑 그래프와 그걸 쓰는 서비스(트리거·승인·조회).

    fetch_boxoffice → compare_prev → draft ⇄ validate → notify_reviewer → request_approval → deliver
                                            (한 번 다시 씀)

- 숫자는 모델이 쓰지 않는다. 표는 report.render가 원자료로 찍고, 모델 글에 숫자가 있으면 validate가 막는다.
- 실패(KOBIS 오류, 대상 주와 다른 집계 기간, 두 번 쓴 초안도 검사 실패)하면 발송하지 않고 failed로 남긴다.
  같은 주에 다시 트리거하면 failed인 것만 처음부터 다시 돈다(KOBIS 집계가 늦은 월요일 아침 대비).

interrupt 동작에서 지킬 것 — interrupt()를 부른 노드는 재개될 때 **처음부터 다시 실행**된다. 그래서
request_approval 안에는 interrupt() 앞에 부작용(발송·알림·DB 쓰기)을 두지 않는다. 알림·발송은 각자 다른 노드다.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from typing import Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from briefing.report import check_period, compare, render, validate_draft
from briefing.store import BriefingStore
from kobis_format import parse_weekly
from logging_config import get_logger

log = get_logger("briefing")

MAX_DRAFT_ATTEMPTS = 2  # 처음 + 검사에 걸렸을 때 한 번 더


class BriefingState(TypedDict, total=False):
    thread_id: str
    week_start: str
    week_end: str
    report: dict
    draft: dict
    problems: list[str]
    attempts: int
    failed: bool
    decision: str  # approve / edit / reject


class Sender(Protocol):
    async def send(self, text: str) -> str:
        """발송하고 채널 쪽 메시지 ID를 준다. 실패하면 예외."""


class LogSender:
    """발송 채널이 없을 때(DISCORD_WEBHOOK_URL 없음). 실제로 보내지 않고 기록만 한다."""

    async def send(self, text: str) -> str:
        delivery_id = f"log-{uuid.uuid4().hex[:12]}"
        log.info("briefing.sent", channel="log", chars=len(text), delivery_id=delivery_id)
        return delivery_id


FetchFn = Callable[[str], Awaitable[dict]]  # YYYYMMDD → KOBIS 주간 원본 응답. 실패면 예외
WriteFn = Callable[[dict, list[str] | None], Awaitable[dict]]  # (report, 고칠 점) → 초안 dict


def build_graph(
    checkpointer,
    store: BriefingStore,
    fetch_fn: FetchFn,
    write_fn: WriteFn,
    sender: Sender,
    notifier: Sender | None = None,
    review_url: str = "",
):
    async def fail(state: BriefingState, reason: str) -> dict:
        await store.set_failed(state["thread_id"], reason[:500])
        log.warning("briefing.failed", thread_id=state["thread_id"], reason=reason[:120])
        return {"failed": True}

    async def fetch_boxoffice(state: BriefingState) -> dict:
        end = date.fromisoformat(state["week_end"])
        try:
            this_week = parse_weekly(await fetch_fn(end.strftime("%Y%m%d")))
        except Exception as e:
            return await fail(state, f"KOBIS 조회 실패({type(e).__name__})")
        if this_week is None:
            return await fail(state, "KOBIS 주간 박스오피스가 비어 있습니다(아직 집계 전일 수 있음).")
        problem = check_period(this_week, state["week_start"], state["week_end"])
        if problem:
            return await fail(state, problem)
        try:  # 전주는 비교용이라 실패해도 이번 주 브리핑은 만든다
            prev_week = parse_weekly(await fetch_fn((end - timedelta(days=7)).strftime("%Y%m%d")))
        except Exception as e:
            log.warning("briefing.prev_week_missing", error=type(e).__name__)
            prev_week = None
        return {"report": compare(this_week, prev_week), "failed": False}

    async def draft(state: BriefingState) -> dict:
        attempts = state.get("attempts", 0) + 1
        try:
            d = await write_fn(state["report"], state.get("problems") or None)
        except Exception as e:
            return {**(await fail(state, f"초안 작성 실패({type(e).__name__})")), "attempts": attempts}
        return {"draft": d, "attempts": attempts, "problems": []}

    async def validate(state: BriefingState) -> dict:
        problems = validate_draft(state["draft"], state["report"])
        log.info("briefing.validated", thread_id=state["thread_id"], attempt=state["attempts"], problems=len(problems))
        if problems and state["attempts"] >= MAX_DRAFT_ATTEMPTS:
            return {**(await fail(state, "초안 검사 실패: " + " / ".join(problems))), "problems": problems}
        if problems:
            return {"problems": problems}
        text = render(state["report"], state["draft"])
        await store.set_pending(state["thread_id"], text, state["attempts"])
        log.info("briefing.pending", thread_id=state["thread_id"], chars=len(text))
        return {"problems": []}

    async def notify_reviewer(state: BriefingState) -> dict:
        # 알림이 실패해도 브리핑은 승인 대기로 남는다(관리자 화면에서 볼 수 있다).
        if notifier is not None:
            try:
                await notifier.send(f"승인 대기: {state['week_start']} ~ {state['week_end']} 주간 브리핑 {review_url}".strip())
            except Exception as e:
                log.warning("briefing.notify_failed", error=type(e).__name__)
        return {}

    async def request_approval(state: BriefingState) -> dict:
        # 여기서 멈추고 상태가 체크포인터에 저장된다. 재개 값은 승인 API가 briefing_runs에 결정을
        # 기록한 뒤 넘긴다. 이 노드는 재개 때 처음부터 다시 돌므로 interrupt() 앞에 부작용을 두지 않는다.
        decision = interrupt({"thread_id": state["thread_id"]})
        return {"decision": decision}

    async def deliver(state: BriefingState) -> dict:
        thread_id = state["thread_id"]
        claimed = await store.claim_send(thread_id)
        if claimed is None:
            # 다른 실행이 이미 보냈거나 보내는 중이다(승인이 두 번 와서 그래프가 두 번 재개된 경우).
            log.info("briefing.send_skipped", thread_id=thread_id)
            return {}
        try:
            delivery_id = await sender.send(claimed["final_text"])
        except Exception as e:
            await store.set_failed(thread_id, f"발송 실패({type(e).__name__})")
            log.warning("briefing.send_failed", thread_id=thread_id, error=type(e).__name__)
            return {}
        await store.mark_sent(thread_id, delivery_id)
        return {}

    def ok_or_end(next_node: str):
        return lambda state: END if state.get("failed") else next_node

    def after_validate(state: BriefingState) -> str:
        if state.get("failed"):
            return END
        return "draft" if state.get("problems") else "notify_reviewer"

    async def compare_prev(state: BriefingState) -> dict:
        # 비교는 fetch_boxoffice에서 원자료를 받자마자 계산해 report에 담았다. 계획서의 노드 이름을 그래프에 남겨
        # LangSmith 트레이스에서 단계가 보이게만 한다.
        return {}

    g = StateGraph(BriefingState)
    for name, fn in [
        ("fetch_boxoffice", fetch_boxoffice), ("compare_prev", compare_prev), ("draft", draft),
        ("validate", validate), ("notify_reviewer", notify_reviewer), ("request_approval", request_approval),
        ("deliver", deliver),
    ]:
        g.add_node(name, fn)
    g.add_edge(START, "fetch_boxoffice")
    g.add_conditional_edges("fetch_boxoffice", ok_or_end("compare_prev"), ["compare_prev", END])
    g.add_edge("compare_prev", "draft")
    g.add_conditional_edges("draft", ok_or_end("validate"), ["validate", END])
    g.add_conditional_edges("validate", after_validate, ["draft", "notify_reviewer", END])
    g.add_edge("notify_reviewer", "request_approval")
    g.add_conditional_edges(
        "request_approval", lambda s: "deliver" if s.get("decision") in ("approve", "edit") else END, ["deliver", END]
    )
    g.add_edge("deliver", END)
    return g.compile(checkpointer=checkpointer)


class DecisionConflict(Exception):
    """이미 결정됐거나 만료된 브리핑에 결정을 보냈다(HTTP 409)."""


class BriefingService:
    def __init__(self, graph, store: BriefingStore, approval_ttl_hours: float):
        self.graph = graph
        self.store = store
        self.ttl = approval_ttl_hours

    @staticmethod
    def _config(thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}}

    async def run(self, thread_id: str, week_start, week_end) -> tuple[dict, bool]:
        """이번 주 브리핑을 시작한다. 이미 있으면 새로 시작하지 않고 기존 상태를 준다(멱등)."""
        created = await self.store.create_if_absent(thread_id, week_start, week_end)
        # 실패한 주는 다시 트리거하면 처음부터 다시 돈다(월요일 아침 KOBIS 집계가 늦었을 때). 승인 대기·발송된 건은 그대로.
        retried = not created and await self.store.reset_failed(thread_id)
        if created or retried:
            log.info("briefing.start", thread_id=thread_id, retry=retried)
            await self.graph.ainvoke(
                # 같은 thread를 다시 돌리면 이전 실행의 상태가 남아 있으므로 시도 횟수 등을 명시적으로 비운다.
                {"thread_id": thread_id, "week_start": str(week_start), "week_end": str(week_end),
                 "attempts": 0, "problems": [], "failed": False, "decision": ""},
                self._config(thread_id),
            )
        return await self.store.get(thread_id), created

    async def list_recent(self) -> list[dict]:
        await self.store.expire_stale(self.ttl)
        return await self.store.list_recent()

    async def get(self, thread_id: str) -> dict | None:
        await self.store.expire_stale(self.ttl)
        return await self.store.get(thread_id)

    async def decide(self, thread_id: str, action: str, text: str | None, by: str) -> dict:
        """결정을 기록하고 그래프를 재개한다.

        기록(조건부 UPDATE)이 먼저다. 두 요청이 동시에 와도 기록은 하나만 성공하고, 나머지는 409.
        단, 기록은 됐는데 재개 전에 프로세스가 죽었다면(approved인데 그래프는 아직 멈춰 있음) 같은 결정을
        다시 보낼 때 재개만 한다 — 발송은 claim_send가 한 번으로 막는다.
        """
        await self.store.expire_stale(self.ttl)
        current = await self.store.get(thread_id)
        if current is None:
            raise KeyError(thread_id)
        approve = action in ("approve", "edit")
        final_text = (text or "").strip() if action == "edit" else current["draft"]
        if action == "edit" and not final_text:
            raise ValueError("수정본이 비어 있습니다.")

        row = await self.store.decide(thread_id, approve, final_text if approve else None, by)
        if row is None:
            if not await self._waiting(thread_id) or current["status"] not in ("approved", "rejected"):
                raise DecisionConflict(current["status"])
            log.info("briefing.resume_retry", thread_id=thread_id)
            action = "approve" if current["status"] == "approved" else "reject"
        log.info("briefing.decided", thread_id=thread_id, action=action)
        await self.graph.ainvoke(Command(resume=action), self._config(thread_id))
        return await self.store.get(thread_id)

    async def _waiting(self, thread_id: str) -> bool:
        snapshot = await self.graph.aget_state(self._config(thread_id))
        return bool(snapshot.next)

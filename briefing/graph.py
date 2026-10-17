"""브리핑 그래프와 그걸 쓰는 서비스(트리거·승인·조회).

interrupt 동작에서 지킬 것 — interrupt()를 부른 노드는 재개될 때 **처음부터 다시 실행**된다. 그래서
request_approval 안에는 interrupt() 앞에 부작용(발송·과금·DB 쓰기)을 두지 않는다. 초안 생성과 발송은
각자 다른 노드다.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from briefing.store import BriefingStore
from logging_config import get_logger

log = get_logger("briefing")


class BriefingState(TypedDict, total=False):
    thread_id: str
    week_start: str
    week_end: str
    draft: str
    decision: str  # approve / edit / reject


class Sender(Protocol):
    async def send(self, text: str) -> str:
        """발송하고 채널 쪽 메시지 ID를 준다. 실패하면 예외."""


class LogSender:
    """3단계용 발송기. 실제로 보내지 않고 기록만 한다(4단계에서 Discord 웹훅으로 바꾼다)."""

    async def send(self, text: str) -> str:
        delivery_id = f"log-{uuid.uuid4().hex[:12]}"
        log.info("briefing.sent", channel="log", chars=len(text), delivery_id=delivery_id)
        return delivery_id


DraftFn = Callable[[str, str], Awaitable[str]]  # (week_start, week_end) → 초안. 실패면 [TOOL_ERROR …]


def build_graph(checkpointer, store: BriefingStore, draft_fn: DraftFn, sender: Sender):
    async def make_draft(state: BriefingState) -> dict:
        text = await draft_fn(state["week_start"], state["week_end"])
        if text.startswith("[TOOL_ERROR"):
            await store.set_failed(state["thread_id"], text[:500])
            log.warning("briefing.draft_failed", thread_id=state["thread_id"])
            return {"draft": "", "decision": "reject"}
        await store.set_pending(state["thread_id"], text)
        log.info("briefing.pending", thread_id=state["thread_id"], chars=len(text))
        return {"draft": text}

    def after_draft(state: BriefingState) -> str:
        return "request_approval" if state.get("draft") else END

    async def request_approval(state: BriefingState) -> dict:
        # 여기서 멈추고 상태가 체크포인터에 저장된다. 재개 값은 승인 API가 briefing_runs에 결정을
        # 기록한 뒤 넘긴다. 이 노드는 재개 때 처음부터 다시 돌므로 interrupt() 앞에 부작용을 두지 않는다.
        decision = interrupt({"thread_id": state["thread_id"], "draft": state["draft"]})
        return {"decision": decision}

    def after_approval(state: BriefingState) -> str:
        return "deliver" if state.get("decision") in ("approve", "edit") else END

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
            await store.set_failed(thread_id, type(e).__name__)
            log.warning("briefing.send_failed", thread_id=thread_id, error=type(e).__name__)
            return {}
        await store.mark_sent(thread_id, delivery_id)
        return {}

    g = StateGraph(BriefingState)
    g.add_node("make_draft", make_draft)
    g.add_node("request_approval", request_approval)
    g.add_node("deliver", deliver)
    g.add_edge(START, "make_draft")
    g.add_conditional_edges("make_draft", after_draft, ["request_approval", END])
    g.add_conditional_edges("request_approval", after_approval, ["deliver", END])
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
        if created:
            log.info("briefing.start", thread_id=thread_id)
            await self.graph.ainvoke(
                {"thread_id": thread_id, "week_start": str(week_start), "week_end": str(week_end)},
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

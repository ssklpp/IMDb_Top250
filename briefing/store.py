"""briefing_runs 테이블. 상태 전이는 전부 조건부 UPDATE ... WHERE status = ... 다.

    drafted → pending → approved → sending → sent
                      ↘ rejected            ↘ failed
                      ↘ expired

같은 요청이 두 번 와도(승인 버튼 두 번, 재시도, 트리거 두 번) 조건을 만족하는 쪽은 하나뿐이라 한 번만 진행된다.
그래프 노드가 다시 실행돼도(interrupt 재개는 노드를 처음부터 다시 돈다) 여기서 걸러진다.
"""

from psycopg_pool import AsyncConnectionPool

SCHEMA = """
CREATE TABLE IF NOT EXISTS briefing_runs (
    thread_id   TEXT PRIMARY KEY,
    week_start  DATE NOT NULL,
    week_end    DATE NOT NULL,
    status      TEXT NOT NULL,
    draft       TEXT,
    final_text  TEXT,
    decided_by  TEXT,
    decided_at  TIMESTAMPTZ,
    sent_at     TIMESTAMPTZ,
    delivery_id TEXT,
    error       TEXT,
    draft_attempts INT,
    pending_at  TIMESTAMPTZ,
    trigger     TEXT,
    prep_seconds REAL,
    llm_tokens  INT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

COLUMNS = (
    "thread_id, week_start, week_end, status, draft, final_text, decided_by, decided_at, "
    "sent_at, delivery_id, error, draft_attempts, pending_at, trigger, prep_seconds, llm_tokens, created_at, updated_at"
)


METRICS_SQL = """
SELECT
    count(*) AS total,
    count(*) FILTER (WHERE status = 'sent') AS sent,
    count(*) FILTER (WHERE status = 'pending') AS pending,
    count(*) FILTER (WHERE status = 'rejected') AS rejected,
    count(*) FILTER (WHERE status = 'expired') AS expired,
    count(*) FILTER (WHERE status = 'failed') AS failed,
    count(*) FILTER (WHERE status IN ('approved', 'sending', 'sent')) AS approved,
    count(*) FILTER (WHERE status IN ('approved', 'sending', 'sent') AND final_text = draft) AS approved_unedited,
    count(*) FILTER (WHERE draft_attempts IS NOT NULL) AS drafted,
    count(*) FILTER (WHERE draft_attempts > 1) AS redrafted,
    count(*) FILTER (WHERE status = 'failed' AND error LIKE '초안 검사 실패%%') AS validate_failed,
    avg(extract(epoch FROM decided_at - pending_at) / 3600) AS hours_avg,
    max(extract(epoch FROM decided_at - pending_at) / 3600) AS hours_max,
    avg(prep_seconds) AS prep_avg,
    avg(llm_tokens) AS tokens_avg
FROM briefing_runs
"""


def _round(v):
    return None if v is None else round(float(v), 2)


class BriefingStore:
    def __init__(self, pool: AsyncConnectionPool):
        self.pool = pool

    async def _one(self, sql: str, params: tuple = ()) -> dict | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(sql, params)
            return await cur.fetchone()

    async def setup(self) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(SCHEMA)
            # 3단계에서 만든 테이블에 4단계 칼럼을 더한다. 이미 있으면 아무 일도 안 한다.
            for column in ("draft_attempts INT", "pending_at TIMESTAMPTZ", "trigger TEXT", "prep_seconds REAL", "llm_tokens INT"):
                await conn.execute(f"ALTER TABLE briefing_runs ADD COLUMN IF NOT EXISTS {column}")

    async def create_if_absent(self, thread_id: str, week_start, week_end) -> bool:
        """이번 주 브리핑을 처음 만들면 True. 이미 있으면 False(트리거가 두 번 와도 하나만 생긴다)."""
        row = await self._one(
            "INSERT INTO briefing_runs (thread_id, week_start, week_end, status) VALUES (%s, %s, %s, 'drafted') "
            "ON CONFLICT (thread_id) DO NOTHING RETURNING thread_id",
            (thread_id, week_start, week_end),
        )
        return row is not None

    async def get(self, thread_id: str) -> dict | None:
        return await self._one(f"SELECT {COLUMNS} FROM briefing_runs WHERE thread_id = %s", (thread_id,))

    async def list_recent(self, limit: int = 20) -> list[dict]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {COLUMNS} FROM briefing_runs ORDER BY created_at DESC LIMIT %s", (limit,)
            )
            return await cur.fetchall()

    async def set_pending(self, thread_id: str, draft: str, attempts: int | None = None) -> None:
        """초안이 나오면 승인 대기로. drafted일 때만 — 노드가 다시 돌아도 승인된 건을 되돌리지 않는다.
        attempts는 초안을 몇 번 만에 검사를 통과했는지(모델이 숫자를 쓰는 빈도를 보는 운영 지표)."""
        await self._one(
            "UPDATE briefing_runs SET status = 'pending', draft = %s, draft_attempts = %s, pending_at = now(), "
            "updated_at = now() "
            "WHERE thread_id = %s AND status = 'drafted' RETURNING thread_id",
            (draft, attempts, thread_id),
        )

    async def record_run(self, thread_id: str, trigger: str, prep_seconds: float, llm_tokens: int) -> None:
        """초안까지 걸린 시간과 LLM 토큰(브리핑 1건당 비용의 근거). 다시 돌리면 덮어쓴다."""
        await self._one(
            "UPDATE briefing_runs SET trigger = %s, prep_seconds = %s, llm_tokens = %s WHERE thread_id = %s "
            "RETURNING thread_id",
            (trigger, prep_seconds, llm_tokens, thread_id),
        )

    async def metrics(self) -> dict:
        """운영 지표(docs/roadmap.md 5절). 비율의 분모가 0이면 None."""
        row = await self._one(METRICS_SQL)
        def ratio(n, d):
            return round(n / d, 3) if d else None
        return {
            "total": row["total"],
            "by_status": {k: row[k] for k in ("sent", "pending", "rejected", "expired", "failed")},
            # 초안 품질: 승인된 것 중 손대지 않고 보낸 비율. 수정본(edit)은 final_text가 초안과 다르다.
            "approved_unedited_ratio": ratio(row["approved_unedited"], row["approved"]),
            # 모델이 규칙(숫자 금지 등)을 처음부터 지키지 못한 비율 — 다시 썼거나 두 번 다 걸린 것
            "redraft_ratio": ratio(row["redrafted"] + row["validate_failed"], row["drafted"] + row["validate_failed"]),
            "validate_failed": row["validate_failed"],
            # 만료 기준(BRIEFING_APPROVAL_TTL_HOURS)을 정하는 근거
            "hours_to_decision_avg": _round(row["hours_avg"]),
            "hours_to_decision_max": _round(row["hours_max"]),
            "prep_seconds_avg": _round(row["prep_avg"]),
            "llm_tokens_avg": _round(row["tokens_avg"]),
        }

    async def reset_failed(self, thread_id: str) -> bool:
        """실패한 브리핑을 다시 돌릴 수 있게 drafted로. failed일 때만."""
        row = await self._one(
            "UPDATE briefing_runs SET status = 'drafted', error = NULL, draft = NULL, updated_at = now() "
            "WHERE thread_id = %s AND status = 'failed' RETURNING thread_id",
            (thread_id,),
        )
        return row is not None

    async def set_failed(self, thread_id: str, error: str) -> None:
        await self._one(
            "UPDATE briefing_runs SET status = 'failed', error = %s, updated_at = now() "
            "WHERE thread_id = %s AND status IN ('drafted', 'sending') RETURNING thread_id",
            (error, thread_id),
        )

    async def expire_stale(self, ttl_hours: float) -> int:
        """승인 대기가 ttl을 넘기면 expired. 만료된 건은 승인해도 발송되지 않는다."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE briefing_runs SET status = 'expired', updated_at = now() "
                "WHERE status = 'pending' AND updated_at < now() - make_interval(secs => %s)",
                (ttl_hours * 3600,),
            )
            return cur.rowcount

    async def decide(self, thread_id: str, approve: bool, final_text: str | None, by: str) -> dict | None:
        """pending일 때만 결정을 기록한다. 이미 결정됐거나 만료됐으면 None — 승인이 두 번 와도 한 번만 진행된다."""
        return await self._one(
            "UPDATE briefing_runs SET status = %s, final_text = %s, decided_by = %s, decided_at = now(), "
            "updated_at = now() WHERE thread_id = %s AND status = 'pending' RETURNING " + COLUMNS,
            ("approved" if approve else "rejected", final_text, by, thread_id),
        )

    async def claim_send(self, thread_id: str) -> dict | None:
        """approved → sending. 이걸 따낸 쪽만 발송한다. 발송 중복의 유일한 방어선이다."""
        return await self._one(
            "UPDATE briefing_runs SET status = 'sending', updated_at = now() "
            "WHERE thread_id = %s AND status = 'approved' RETURNING final_text",
            (thread_id,),
        )

    async def mark_sent(self, thread_id: str, delivery_id: str) -> None:
        await self._one(
            "UPDATE briefing_runs SET status = 'sent', sent_at = now(), delivery_id = %s, updated_at = now() "
            "WHERE thread_id = %s AND status = 'sending' RETURNING thread_id",
            (delivery_id, thread_id),
        )

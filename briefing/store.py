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
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

COLUMNS = (
    "thread_id, week_start, week_end, status, draft, final_text, decided_by, decided_at, "
    "sent_at, delivery_id, error, draft_attempts, created_at, updated_at"
)


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
            await conn.execute("ALTER TABLE briefing_runs ADD COLUMN IF NOT EXISTS draft_attempts INT")

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
            "UPDATE briefing_runs SET status = 'pending', draft = %s, draft_attempts = %s, updated_at = now() "
            "WHERE thread_id = %s AND status = 'drafted' RETURNING thread_id",
            (draft, attempts, thread_id),
        )

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

"""브리핑 관리자 API. 관리자 토큰(ADMIN_TOKEN)이 있어야 하고, Postgres가 없으면 꺼져 있다(503).

    POST /briefings/run                   이번 주 브리핑 시작(이미 있으면 기존 상태)
    GET  /briefings                       최근 목록(승인 대기 만료 처리 포함)
    GET  /briefings/metrics               운영 지표(수정 없이 승인된 비율, 재작성률, 승인까지 시간, 토큰·시간)
    GET  /briefings/{thread_id}
    POST /briefings/{thread_id}/decision  {"action": "approve"|"edit"|"reject", "text": 수정본, "by": 이름}
"""

import hmac
import os
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from briefing.graph import BriefingService, DecisionConflict
from kobis_format import briefing_week, today_kst

router = APIRouter(prefix="/briefings")


def _service(request: Request) -> BriefingService:
    service = getattr(request.app.state, "briefing", None)
    if service is None:
        raise HTTPException(503, {"code": "BRIEFING_DISABLED", "error": "브리핑은 Postgres(DATABASE_URL)가 있을 때만 켜집니다."})
    return service


def _require_admin(request: Request) -> None:
    token = os.environ.get("ADMIN_TOKEN", "")
    if not token:
        raise HTTPException(503, {"code": "ADMIN_DISABLED", "error": "ADMIN_TOKEN이 설정되지 않았습니다."})
    sent = request.headers.get("authorization", "")
    if not hmac.compare_digest(sent.encode(), f"Bearer {token}".encode()):
        raise HTTPException(401, {"code": "UNAUTHORIZED", "error": "관리자 토큰이 맞지 않습니다."})


class Decision(BaseModel):
    action: Literal["approve", "edit", "reject"]
    text: str | None = Field(None, max_length=4000)
    by: str = Field(..., min_length=1, max_length=64)


def _public(row: dict | None) -> dict | None:
    if row is None:
        return None
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()}


@router.post("/run", dependencies=[Depends(_require_admin)])
async def run(trigger: Literal["manual", "n8n"] = "manual", service: BriefingService = Depends(_service)):
    thread_id, monday, sunday = briefing_week(today_kst())
    row, created = await service.run(thread_id, monday, sunday, trigger)
    return {"created": created, "briefing": _public(row)}


@router.get("", dependencies=[Depends(_require_admin)])
async def list_briefings(service: BriefingService = Depends(_service)):
    return {"briefings": [_public(r) for r in await service.list_recent()]}


@router.get("/metrics", dependencies=[Depends(_require_admin)])
async def metrics(service: BriefingService = Depends(_service)):
    """운영 지표. /{thread_id}보다 먼저 등록해야 "metrics"가 thread_id로 잡히지 않는다."""
    return {"metrics": await service.metrics()}


@router.get("/{thread_id}", dependencies=[Depends(_require_admin)])
async def get_briefing(thread_id: str, service: BriefingService = Depends(_service)):
    row = await service.get(thread_id)
    if row is None:
        raise HTTPException(404, {"code": "NOT_FOUND", "error": "그런 브리핑이 없습니다."})
    return {"briefing": _public(row)}


@router.post("/{thread_id}/decision", dependencies=[Depends(_require_admin)])
async def decide(thread_id: str, body: Decision, service: BriefingService = Depends(_service)):
    try:
        row = await service.decide(thread_id, body.action, body.text, body.by)
    except KeyError:
        raise HTTPException(404, {"code": "NOT_FOUND", "error": "그런 브리핑이 없습니다."})
    except ValueError as e:
        raise HTTPException(422, {"code": "INVALID_INPUT", "error": str(e)})
    except DecisionConflict as e:
        raise HTTPException(409, {"code": "ALREADY_DECIDED", "error": f"이미 처리된 브리핑입니다(상태: {e})."})
    return {"briefing": _public(row)}

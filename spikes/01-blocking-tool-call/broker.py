"""Spike broker: the smallest thing that can hold a tool call open.

A real approval request parks on an asyncio.Event and does not resolve until
something calls /decide. In the real system that /decide call comes from the
serial bridge when the Arduino reports a button press; here it comes from the
spike driver so we can test the transport without hardware.
"""

import asyncio
import uuid
from dataclasses import dataclass, field

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


@dataclass
class Pending:
    id: str
    action: str
    justification: str
    event: asyncio.Event = field(default_factory=asyncio.Event)
    approved: bool | None = None
    reason: str = ""


app = FastAPI()
PENDING: dict[str, Pending] = {}


class ApprovalRequest(BaseModel):
    action: str
    justification: str = ""


class Decision(BaseModel):
    approved: bool
    reason: str = ""


@app.post("/request_approval")
async def request_approval(req: ApprovalRequest) -> dict:
    """Blocks until /decide is called for this request. No timeout on our side --
    the physical gate has no timeout either."""
    p = Pending(id=uuid.uuid4().hex[:8], action=req.action, justification=req.justification)
    PENDING[p.id] = p
    await p.event.wait()
    PENDING.pop(p.id, None)
    return {"id": p.id, "approved": p.approved, "reason": p.reason}


@app.post("/decide/{req_id}")
async def decide(req_id: str, d: Decision) -> dict:
    p = PENDING.get(req_id)
    if p is None:
        raise HTTPException(404, f"no pending request {req_id}")
    p.approved = d.approved
    p.reason = d.reason
    p.event.set()
    return {"ok": True}


@app.get("/pending")
async def pending() -> list[dict]:
    return [{"id": p.id, "action": p.action} for p in PENDING.values()]


@app.get("/health")
async def health() -> dict:
    return {"ok": True}

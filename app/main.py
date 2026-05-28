"""
FastAPI application.

Endpoints:
  GET  /health               — liveness check
  POST /webhook              — Jira issue_created / issue_updated events
  POST /batch                — trigger batch processing of open tickets
  GET  /batch/status/{id}    — poll async batch job
"""
import hashlib
import hmac
import time
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel

from agent.config import settings
from agent.orchestrator import run as agent_run
from agent.skills.jira_actions import fetch_tickets, parse_webhook

log = structlog.get_logger(__name__)

# In-memory job store (swap for Redis in production)
_jobs: dict[str, dict] = {}


# ── Startup ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("startup", service="helix-helpdesk-agent")
    # Warm up ChromaDB connection
    try:
        from agent.skills.policy_rag import _get_collection
        col = _get_collection()
        log.info("chroma_warmed", chunks=col.count())
    except Exception as e:
        log.warning("chroma_warmup_failed", error=str(e))
    yield
    log.info("shutdown")


app = FastAPI(
    title="Helix IT Helpdesk Agent",
    description="AI agent that auto-resolves IT tickets grounded in Helix IT policies.",
    version="0.1.0",
    lifespan=lifespan,
)


# ── Health ────────────────────────────────────────────────────────────────

@app.get("/health", tags=["ops"])
def health():
    return {"status": "ok", "ts": int(time.time())}


# ── Webhook ───────────────────────────────────────────────────────────────

def _verify_signature(body: bytes, header: str | None) -> None:
    """Validate HMAC-SHA256 if a webhook secret is set. Skip in dev mode."""
    secret = settings.webhook_secret
    if not secret:
        return
    if not header:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing X-Hub-Signature")
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, header):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid signature")


@app.post("/webhook", tags=["agent"], status_code=status.HTTP_202_ACCEPTED)
async def webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature: str | None = Header(default=None),
):
    """Receive Jira webhook events and process tickets asynchronously."""
    body = await request.body()
    _verify_signature(body, x_hub_signature)

    payload = await request.json()
    ticket = parse_webhook(payload)

    if ticket is None:
        return {"accepted": False, "reason": "not actionable"}

    log.info("webhook_accepted", key=ticket.key)
    background_tasks.add_task(_process_one, ticket)
    return {"accepted": True, "ticket_key": ticket.key}


async def _process_one(ticket) -> None:
    try:
        result = agent_run(ticket)
        log.info("webhook_ticket_done",
                 key=ticket.key, action=result.decision.action)
    except Exception as e:
        log.error("webhook_ticket_error", key=ticket.key, error=str(e))


# ── Batch ─────────────────────────────────────────────────────────────────

class BatchRequest(BaseModel):
    label:       str  = "eval-set"
    max_results: int  = 50
    status:      str  = "To Do"
    dry_run:     bool = False   # compute decisions, skip Jira writes


@app.post("/batch", tags=["agent"])
async def batch(req: BatchRequest, background_tasks: BackgroundTasks):
    """Trigger async batch processing. Returns a job_id to poll for results."""
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "queued", "started_at": time.time(), "results": []}
    background_tasks.add_task(_run_batch, job_id, req)
    return {"job_id": job_id, "status": "queued"}


async def _run_batch(job_id: str, req: BatchRequest) -> None:
    _jobs[job_id]["status"] = "running"
    results = []

    try:
        tickets = fetch_tickets(
            label=req.label,
            max_results=req.max_results,
            status=req.status,
        )
        _jobs[job_id]["total"] = len(tickets)

        for ticket in tickets:
            try:
                result = agent_run(ticket, dry_run=req.dry_run)
                d = result.decision
                results.append({
                    "ticket_key":    result.ticket_key,
                    "action":        d.action,
                    "citation":      d.policy_citation,
                    "reason_code":   d.reason_code,
                    "confidence":    round(d.confidence, 3),
                    "comment_posted": result.comment_posted,
                    "error":         result.error,
                })
            except Exception as e:
                log.error("batch_ticket_error", key=ticket.key, error=str(e))
                results.append({"ticket_key": ticket.key, "error": str(e)})

        _jobs[job_id].update({
            "status": "complete",
            "results": results,
            "completed_at": time.time(),
        })
        log.info("batch_complete", job_id=job_id, total=len(results))

    except Exception as e:
        log.error("batch_failed", job_id=job_id, error=str(e))
        _jobs[job_id].update({"status": "failed", "error": str(e)})


@app.get("/batch/status/{job_id}", tags=["agent"])
def batch_status(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id} not found")
    return job


# ── CLI entry points (poetry run serve / poetry run dev) ──────────────────

def start() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )


def dev() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
    )


if __name__ == "__main__":
    dev()

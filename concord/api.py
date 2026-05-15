"""HTTP API.

Three groups of endpoints:
- POST /support: submit a customer request, get a final response.
- GET /traces, /traces/{id}: inspect requests for the live trace view.
- GET /metrics, /healthz: ops endpoints.

Plus the demo web UI served at /.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from concord.config import get_settings
from concord.models import CustomerContext, SupportRequest
from concord.observability.metrics import get_metrics
from concord.orchestrator import Concord
from concord.retrieval.service import get_retrieval_service
from concord.state import TraceStore, init_db

_log = structlog.get_logger("concord.api")

app = FastAPI(title="Concord", version="0.1.0")
_concord = Concord()
_traces = TraceStore()


class SubmitRequest(BaseModel):
    customer_id: str
    customer_email: str | None = None
    plan: str = "free"
    account_status: str = "active"
    tenure_days: int = 0
    message: str
    conversation_id: str | None = None


@app.on_event("startup")
async def _startup() -> None:
    settings = get_settings()
    _log.info("concord_starting", env=settings.env, model_fast=settings.model_fast)
    await init_db()
    # Index knowledge on startup. Idempotent so safe to repeat.
    try:
        n = get_retrieval_service().index_knowledge_dir()
        _log.info("knowledge_indexed", chunks=n)
    except Exception as exc:
        _log.exception("knowledge_indexing_failed", error=str(exc))


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics")
async def metrics() -> Response:
    body, content_type = get_metrics().render()
    return Response(content=body, media_type=content_type)


@app.post("/support")
async def submit_support(req: SubmitRequest) -> JSONResponse:
    customer = CustomerContext(
        customer_id=req.customer_id,
        email=req.customer_email,
        plan=req.plan,
        account_status=req.account_status,
        tenure_days=req.tenure_days,
    )
    request = SupportRequest(
        customer=customer,
        message=req.message,
        conversation_id=req.conversation_id,
    )
    response = await _concord.handle_request(request)
    return JSONResponse(content=response.model_dump(mode="json"))


@app.get("/traces")
async def list_traces(limit: int = 25) -> list[dict]:
    return await _traces.recent(limit=limit)


@app.get("/traces/{trace_id}")
async def get_trace(trace_id: str) -> dict:
    rec = await _traces.get(trace_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return rec


_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if _WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_WEB_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        index_html = _WEB_DIR / "index.html"
        return HTMLResponse(index_html.read_text(encoding="utf-8"))


@app.exception_handler(Exception)
async def _on_unhandled(request: Request, exc: Exception) -> JSONResponse:
    _log.exception("unhandled_exception", path=request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": "internal error", "request_path": request.url.path},
    )

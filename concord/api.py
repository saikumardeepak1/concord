"""HTTP API.

Three groups of endpoints:
- POST /support: submit a customer request, get a final response.
- GET /traces, /traces/{id}: inspect requests for the live trace view.
- GET /metrics, /healthz: ops endpoints.

Plus the demo web UI served at /.
"""

from __future__ import annotations

import os
from pathlib import Path

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from concord.config import get_settings
from concord.customers import CustomerNotFoundError, get_directory
from concord.models import SupportRequest
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
    message: str
    conversation_id: str | None = None
    # NOTE: plan / account_status / tenure_days are NO LONGER trusted from the
    # request body. They come from the verified directory lookup. Demo testers
    # cannot fake their account state by sending a different customer_id; the
    # only valid IDs are cust-001 through cust-006 (see /customers endpoint).


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
    # Identity verification gate. In production this would validate a JWT
    # from the chat widget against the company identity provider. In the
    # demo we resolve against the mock directory; unknown IDs are rejected
    # so reviewers can see the gate working.
    try:
        record = get_directory().verify(req.customer_id)
    except CustomerNotFoundError as exc:
        return JSONResponse(
            status_code=401,
            content={
                "error": "customer_not_found",
                "message": str(exc),
                "hint": "GET /customers for the list of valid demo customer IDs.",
            },
        )
    customer = record.to_context()
    request = SupportRequest(
        customer=customer,
        message=req.message,
        conversation_id=req.conversation_id,
    )
    response = await _concord.handle_request(request)
    return JSONResponse(content=response.model_dump(mode="json"))


@app.get("/customers")
async def list_customers() -> list[dict]:
    """Demo helper. Returns the list of verified test customers and their
    state. Use this in the web UI dropdown and to know which customer_id to
    pass to /support. Not present in a real deployment — the real CRM would
    not expose every customer over an unauthenticated endpoint.
    """
    directory = get_directory()
    out: list[dict] = []
    for cid in directory.known_customer_ids():
        rec = directory.verify(cid)
        out.append({
            "customer_id": rec.customer_id,
            "name": rec.name,
            "email": rec.email,
            "plan": rec.plan,
            "account_status": rec.account_status,
            "tenure_days": rec.tenure_days,
            "recent_transactions": [t.to_dict() for t in rec.transactions[:5]],
        })
    return out


@app.get("/traces")
async def list_traces(limit: int = 25) -> list[dict]:
    return await _traces.recent(limit=limit)


@app.get("/traces/{trace_id}")
async def get_trace(trace_id: str) -> dict:
    rec = await _traces.get(trace_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return rec


def _find_web_dir() -> Path | None:
    """Locate the web/ directory across editable, source, and packaged installs.

    Local dev with `pip install -e .` lives at `<repo>/concord/api.py` so the
    relative-to-source path works. In production Docker the package is
    installed into site-packages and `web/` sits at `/app/web` (the WORKDIR),
    not next to the installed module. Check both, plus an env-var escape hatch.
    """
    env_dir = os.environ.get("CONCORD_WEB_DIR")
    candidates = [
        Path(env_dir) if env_dir else None,
        Path.cwd() / "web",
        Path(__file__).resolve().parent.parent / "web",
        Path(__file__).resolve().parent / "web",
        Path("/app/web"),
    ]
    for c in candidates:
        if c is not None and c.exists() and (c / "index.html").exists():
            return c
    return None


_WEB_DIR = _find_web_dir()
if _WEB_DIR is not None:
    _log.info("web_dir_resolved", path=str(_WEB_DIR))
    app.mount("/static", StaticFiles(directory=str(_WEB_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        index_html = _WEB_DIR / "index.html"
        return HTMLResponse(index_html.read_text(encoding="utf-8"))
else:
    _log.warning("web_dir_not_found", searched=[
        os.environ.get("CONCORD_WEB_DIR"),
        str(Path.cwd() / "web"),
        str(Path(__file__).resolve().parent.parent / "web"),
        str(Path(__file__).resolve().parent / "web"),
        "/app/web",
    ])


@app.exception_handler(Exception)
async def _on_unhandled(request: Request, exc: Exception) -> JSONResponse:
    _log.exception("unhandled_exception", path=request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": "internal error", "request_path": request.url.path},
    )

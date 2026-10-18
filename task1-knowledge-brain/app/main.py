"""Knowledge Brain HTTP API. Run: uvicorn app.main:create_app --factory --port 8001   (docs at /docs)"""
from __future__ import annotations

import asyncio
import hmac
import logging
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from ingestion.pipeline import run_ingestion

from .config import Settings, get_settings
from .llm import LLM
from .models import (Escalation, EscalationCreate, EscalationResolve, Health, IngestionReport, IngestionRunRequest, QueryRequest,
                     QueryResponse)
from .service import KnowledgeService
from .store import Store

log = logging.getLogger("kb")


def create_app(settings: Optional[Settings] = None, store: Optional[Store] = None, llm: Optional[LLM] = None) -> FastAPI:
    s = settings or get_settings()
    store = store or Store(s.db_path)
    llm = llm or LLM(s)
    svc = KnowledgeService(s, store, llm)
    app = FastAPI(title="Knowledge Brain", version="1.0",
                  description="Shared knowledge service: grounded answers with citations, ingestion, human escalation.")
    app.state.svc, app.state.store, app.state.settings = svc, store, s
    ingest_lock = asyncio.Lock()

    def auth(x_api_key: Optional[str] = Header(default=None)) -> None:
        if s.api_key and not (x_api_key and hmac.compare_digest(x_api_key, s.api_key)):
            raise HTTPException(401, "invalid or missing X-API-Key")

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        t0 = time.perf_counter()
        try:
            resp = await call_next(request)
        except Exception:  # noqa: BLE001
            log.exception("unhandled error rid=%s", rid)
            resp = JSONResponse({"detail": "internal error", "request_id": rid}, status_code=500)
        resp.headers["x-request-id"] = rid
        resp.headers["x-latency-ms"] = f"{(time.perf_counter() - t0) * 1000:.0f}"
        return resp

    @app.get("/", include_in_schema=False)
    def chat_ui() -> FileResponse:
        return FileResponse(Path(__file__).parent / "static" / "index.html")

    @app.get("/health", response_model=Health)
    def health() -> Health:
        c = store.counts()
        return Health(status="ok", llm=llm.chain_label, llm_notes=llm.chain.notes, active_pages=c["pages"], active_chunks=c["chunks"], open_escalations=c["open_escalations"])

    @app.post("/query", response_model=QueryResponse, dependencies=[Depends(auth)])
    def query(req: QueryRequest) -> QueryResponse:
        return svc.query(req)

    async def _job(body: IngestionRunRequest) -> None:
        async with ingest_lock:
            await run_ingestion(s, store, llm, limit=body.limit, url_filter=body.url_filter)

    @app.post("/ingestion/run", status_code=202, dependencies=[Depends(auth)])
    async def ingestion_run(body: IngestionRunRequest, bg: BackgroundTasks) -> dict:
        if ingest_lock.locked():
            raise HTTPException(409, "an ingestion job is already running")
        bg.add_task(_job, body)
        return {"accepted": True}

    @app.get("/ingestion/status", response_model=list[IngestionReport], dependencies=[Depends(auth)])
    def ingestion_status(limit: int = 5) -> list[IngestionReport]:
        rows = store.q("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (min(max(limit, 1), 50),))
        return [IngestionReport(job_id=r["id"], state=r["state"], added=r["added"], updated=r["updated"], removed=r["removed"],
                                unchanged=r["unchanged"], failed=r["failed"], started_at=r["started_at"], finished_at=r["finished_at"]) for r in rows]

    @app.post("/escalations", response_model=Escalation, status_code=201, dependencies=[Depends(auth)])
    def create_escalation(body: EscalationCreate) -> Escalation:
        return svc.get_escalation(svc.create_escalation(body.question, body.reason))

    @app.get("/escalations", response_model=list[Escalation], dependencies=[Depends(auth)])
    def list_escalations(status: Optional[str] = None) -> list[Escalation]:
        return svc.list_escalations(status)

    @app.post("/escalations/{esc_id}/resolve", dependencies=[Depends(auth)])
    def resolve(esc_id: int, body: EscalationResolve) -> dict:
        out = svc.resolve_escalation(esc_id, body)
        if out is None:
            raise HTTPException(404, "escalation not found")
        return out

    return app

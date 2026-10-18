"""Meeting-to-Scope API. Run: uvicorn app.main:create_app --factory --port 8003"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from .config import ROOT, Settings, get_settings
from .kb_client import KbClient
from .llm import LLM
from .models import ApproveRequest, EditRequest, MeetingCreate, ProjectCreate, SendRequest
from .pipeline import PipelineError, ScopePipeline
from .store import Store


def create_app(settings: Optional[Settings] = None, store: Optional[Store] = None, kb: Optional[KbClient] = None, llm: Optional[LLM] = None) -> FastAPI:
    s = settings or get_settings()
    store = store or Store(s.db_path)
    kb = kb or KbClient(s.kb_url, s.kb_api_key, s.kb_timeout_s)
    llm = llm or LLM(s)
    pipe = ScopePipeline(s, store, kb, llm)
    app = FastAPI(title="Meeting to Scope", version="1.0")
    app.state.pipe, app.state.store = pipe, store

    @app.exception_handler(PipelineError)
    async def _pe(_, exc: PipelineError):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "llm": llm.chain_label, "llm_notes": llm.chain.notes, "knowledge": kb.health()}

    @app.get("/", include_in_schema=False)
    def ui() -> FileResponse:
        return FileResponse(ROOT / "ui" / "index.html")

    @app.get("/samples")
    def samples() -> list[str]:
        return sorted(p.name for p in (ROOT / "transcripts").glob("*.txt"))

    @app.get("/samples/{name}", response_class=PlainTextResponse)
    def sample(name: str) -> str:
        p = (ROOT / "transcripts" / Path(name).name)
        if not p.is_file():
            raise HTTPException(404, "sample not found")
        return p.read_text(encoding="utf-8")

    @app.post("/projects", status_code=201)
    def create_project(b: ProjectCreate) -> dict:
        return {"id": store.create_project(b.name, b.client), "name": b.name, "client": b.client}

    @app.get("/projects")
    def projects() -> list[dict]:
        return [dict(r) for r in store.q("SELECT * FROM projects ORDER BY id DESC")]

    @app.get("/projects/{pid}")
    def project(pid: int) -> dict:
        p = store.project(pid)
        if not p:
            raise HTTPException(404, "project not found")
        return {**dict(p), "meetings": [dict(m) for m in store.meetings(pid)], "versions": store.versions(pid), "audit": store.audit_log(pid)}

    @app.post("/projects/{pid}/meetings", status_code=201)
    def add_meeting(pid: int, b: MeetingCreate) -> dict:
        vid = pipe.process_meeting(pid, b)
        return store.version(vid)

    @app.get("/versions/{vid}")
    def version(vid: int) -> dict:
        v = store.version(vid)
        if not v:
            raise HTTPException(404, "version not found")
        return v

    @app.post("/versions/{vid}/approve")
    def approve(vid: int, b: ApproveRequest) -> dict:
        try:
            v = store.approve(vid, b.reviewer)
        except KeyError:
            raise HTTPException(404, "version not found")
        except ValueError as e:
            raise HTTPException(409, str(e))
        issues = v["proposal"].get("consistency_issues", [])
        return {**v, "approval_warnings": issues}

    @app.post("/versions/{vid}/edit")
    def edit(vid: int, b: EditRequest) -> dict:
        return pipe.apply_edit(vid, b)

    @app.post("/versions/{vid}/send")
    def send(vid: int, b: SendRequest) -> dict:
        try:
            return store.send(vid, b.to)
        except KeyError:
            raise HTTPException(404, "version not found")
        except PermissionError as e:
            raise HTTPException(409, str(e))

    return app

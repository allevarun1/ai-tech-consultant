"""SQLite persistence with versioned scopes, human approval and an audit trail."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, client TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meetings (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, title TEXT NOT NULL, date TEXT NOT NULL, kind TEXT NOT NULL,
  transcript TEXT NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL, extractor TEXT);
CREATE TABLE IF NOT EXISTS versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, version_no INTEGER NOT NULL, meeting_id INTEGER NOT NULL,
  status TEXT NOT NULL, requirements_json TEXT NOT NULL, scope_json TEXT NOT NULL, estimate_json TEXT NOT NULL, proposal_json TEXT NOT NULL,
  change_json TEXT, representation_json TEXT NOT NULL, deadline_iso TEXT, content_hash TEXT NOT NULL,
  approved_by TEXT, approved_at TEXT, approved_hash TEXT, created_at TEXT NOT NULL, UNIQUE(project_id, version_no));
CREATE TABLE IF NOT EXISTS sends (version_id INTEGER PRIMARY KEY, to_addr TEXT NOT NULL, sent_at TEXT NOT NULL, approved_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL, event TEXT NOT NULL, project_id INTEGER, version_id INTEGER, payload_json TEXT);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def content_hash(requirements: Any, scope: Any, estimate: Any, proposal: Any, change: Any) -> str:
    """Hash of everything a client could see. If it changes after approval, the approval no longer applies."""
    blob = json.dumps({"r": requirements, "s": scope, "e": estimate, "p": proposal.get("sections") if isinstance(proposal, dict) else proposal, "c": change}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.c = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.c.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.c.execute("PRAGMA busy_timeout=10000")
        self.c.executescript(SCHEMA)

    def q(self, sql: str, a: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.c.execute(sql, a).fetchall()

    def x(self, sql: str, a: tuple = ()) -> int:
        with self.lock:
            return self.c.execute(sql, a).lastrowid

    def audit(self, actor: str, event: str, project_id: Optional[int] = None, version_id: Optional[int] = None, payload: Any = None) -> None:
        self.x("INSERT INTO audit(ts,actor,event,project_id,version_id,payload_json) VALUES(?,?,?,?,?,?)", (now(), actor, event, project_id, version_id, json.dumps(payload or {}, default=str)))

    # ---- projects / meetings -------------------------------------------------------------------
    def create_project(self, name: str, client: str) -> int:
        pid = self.x("INSERT INTO projects(name,client,created_at) VALUES(?,?,?)", (name, client, now()))
        self.audit("system", "project_created", pid)
        return pid

    def project(self, pid: int) -> Optional[sqlite3.Row]:
        r = self.q("SELECT * FROM projects WHERE id=?", (pid,))
        return r[0] if r else None

    def add_meeting(self, pid: int, title: str, date: str, kind: str, transcript: str, extractor: str) -> int:
        return self.x("INSERT INTO meetings(project_id,title,date,kind,transcript,sha256,created_at,extractor) VALUES(?,?,?,?,?,?,?,?)",
                      (pid, title, date, kind, transcript, hashlib.sha256(transcript.encode()).hexdigest(), now(), extractor))

    def meetings(self, pid: int) -> list[sqlite3.Row]:
        return self.q("SELECT id,title,date,kind,sha256,extractor,created_at FROM meetings WHERE project_id=? ORDER BY date,id", (pid,))

    # ---- versions -------------------------------------------------------------------------------------
    def add_version(self, pid: int, meeting_id: int, requirements: list, scope: dict, estimate: dict, proposal: dict, change: Optional[dict], representation: dict, deadline_iso: Optional[str]) -> int:
        n = self.q("SELECT COALESCE(MAX(version_no),0) n FROM versions WHERE project_id=?", (pid,))[0]["n"] + 1
        h = content_hash(requirements, scope, estimate, proposal, change)
        vid = self.x("INSERT INTO versions(project_id,version_no,meeting_id,status,requirements_json,scope_json,estimate_json,proposal_json,change_json,representation_json,deadline_iso,content_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (pid, n, meeting_id, "draft", json.dumps(requirements), json.dumps(scope), json.dumps(estimate), json.dumps(proposal), json.dumps(change) if change else None, json.dumps(representation), deadline_iso, h, now()))
        self.audit("agent", "version_created", pid, vid, {"version_no": n, "meeting_id": meeting_id})
        return vid

    def version(self, vid: int) -> Optional[dict]:
        r = self.q("SELECT * FROM versions WHERE id=?", (vid,))
        if not r:
            return None
        v = dict(r[0])
        for k in ("requirements", "scope", "estimate", "proposal", "change", "representation"):
            raw = v.pop(f"{k}_json")
            v[k] = json.loads(raw) if raw else None
        v["sent"] = bool(self.q("SELECT 1 FROM sends WHERE version_id=?", (vid,)))
        return v

    def versions(self, pid: int) -> list[dict]:
        return [{k: r[k] for k in ("id", "version_no", "meeting_id", "status", "approved_by", "approved_at", "created_at", "deadline_iso")} for r in self.q("SELECT * FROM versions WHERE project_id=? ORDER BY version_no", (pid,))]

    def approved_baseline(self, pid: int) -> Optional[dict]:
        r = self.q("SELECT id FROM versions WHERE project_id=? AND status='approved' ORDER BY version_no DESC LIMIT 1", (pid,))
        return self.version(r[0]["id"]) if r else None

    def update_version_content(self, vid: int, requirements: list, scope: dict, estimate: dict, proposal: dict, change: Optional[dict]) -> str:
        h = content_hash(requirements, scope, estimate, proposal, change)
        self.x("UPDATE versions SET requirements_json=?,scope_json=?,estimate_json=?,proposal_json=?,change_json=?,content_hash=?,status='draft',approved_by=NULL,approved_at=NULL,approved_hash=NULL WHERE id=?",
               (json.dumps(requirements), json.dumps(scope), json.dumps(estimate), json.dumps(proposal), json.dumps(change) if change else None, h, vid))
        return h

    def approve(self, vid: int, reviewer: str) -> dict:
        v = self.version(vid)
        if not v:
            raise KeyError("version not found")
        if v["status"] == "superseded":
            raise ValueError("a superseded version cannot be approved")
        with self.lock:
            self.c.execute("BEGIN IMMEDIATE")
            try:
                self.c.execute("UPDATE versions SET status='superseded' WHERE project_id=? AND status='approved' AND id<>?", (v["project_id"], vid))
                self.c.execute("UPDATE versions SET status='approved',approved_by=?,approved_at=?,approved_hash=? WHERE id=?", (reviewer, now(), v["content_hash"], vid))
                self.c.execute("COMMIT")
            except BaseException:
                self.c.execute("ROLLBACK")
                raise
        self.audit(reviewer, "version_approved", v["project_id"], vid, {"hash": v["content_hash"]})
        return self.version(vid)

    def send(self, vid: int, to: str) -> dict:
        """Only an approved version whose content has not changed since approval can be sent. Idempotent."""
        v = self.version(vid)
        if not v:
            raise KeyError("version not found")
        if v["status"] != "approved":
            raise PermissionError(f"version is '{v['status']}'; only an approved version can be sent")
        if v["approved_hash"] != v["content_hash"]:
            raise PermissionError("content changed after approval; approve it again")
        with self.lock:
            existing = self.c.execute("SELECT * FROM sends WHERE version_id=?", (vid,)).fetchone()
            if existing:
                return {"already_sent": True, "sent_at": existing["sent_at"], "to": existing["to_addr"]}
            self.c.execute("INSERT INTO sends(version_id,to_addr,sent_at,approved_hash) VALUES(?,?,?,?)", (vid, to, now(), v["approved_hash"]))
        self.audit("system", "version_sent", v["project_id"], vid, {"to": to, "simulated": True})
        return {"already_sent": False, "to": to, "simulated": True}

    def audit_log(self, pid: int) -> list[dict]:
        return [{"ts": r["ts"], "actor": r["actor"], "event": r["event"], "version_id": r["version_id"], "payload": json.loads(r["payload_json"] or "{}")} for r in self.q("SELECT * FROM audit WHERE project_id=? ORDER BY id", (pid,))]

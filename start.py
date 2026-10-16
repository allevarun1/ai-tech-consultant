#!/usr/bin/env python3
"""One-command startup:   python start.py [--ingest N | --full-ingest] [--no-install]

 1. installs Python and Node dependencies if they are missing
 2. ingests a small starter corpus into the Knowledge Brain if its database is empty (default 20 articles)
 3. starts Knowledge Brain :8001, Inbox Agent :8002, Meeting-to-Scope :8003 and waits for each to be healthy
Ctrl-C stops all three. Secrets come from .env (see .env.example); with no ANTHROPIC_API_KEY everything runs
in the documented offline fallback mode.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
T1, T2, T3 = ROOT / "task1-knowledge-brain", ROOT / "task2-inbox-agent", ROOT / "task3-meeting-to-scope"
PORTS = {"knowledge": 8001, "inbox": 8002, "scope": 8003}


def load_env() -> dict:
    env = os.environ.copy()
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if "=" in line:
                k, v = line.split("=", 1)
                if v.strip():
                    env.setdefault(k.strip(), v.strip())
    return env


def run(cmd: list[str], cwd: Path, env: dict, check: bool = True) -> None:
    print(f"$ ({cwd.name}) {' '.join(cmd)}")
    subprocess.run(cmd, cwd=cwd, env=env, check=check)


def healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ingest", type=int, default=20, help="articles to ingest if the knowledge base is empty (default 20)")
    ap.add_argument("--full-ingest", action="store_true", help="ingest the whole sitemap (hundreds of pages, polite rate limit; takes a while)")
    ap.add_argument("--no-install", action="store_true")
    a = ap.parse_args()
    env = load_env()
    py = sys.executable

    if sys.version_info < (3, 11):
        sys.exit("Python 3.11+ required")
    if not shutil.which("node"):
        sys.exit("Node.js 22.18+ required (for the TypeScript Inbox Agent)")

    if not a.no_install:
        for t in (T1, T3):
            run([py, "-m", "pip", "install", "-q", "-r", "requirements.txt"], t, env)
        if not (T2 / "node_modules").exists():
            run([shutil.which("npm") or "npm", "install", "--no-audit", "--no-fund"], T2, env)

    db = T1 / "data" / "kb.sqlite"
    empty = True
    if db.exists():
        import sqlite3

        try:
            empty = sqlite3.connect(db).execute("select count(*) from articles where active=1").fetchone()[0] == 0
        except sqlite3.Error:
            empty = True
    if empty:
        args = [py, "-m", "ingestion.cli"] + ([] if a.full_ingest else ["--limit", str(a.ingest)])
        run(args, T1, env)

    procs: list[subprocess.Popen] = []
    try:
        procs.append(subprocess.Popen([py, "-m", "uvicorn", "app.main:create_app", "--factory", "--port", "8001", "--log-level", "warning"], cwd=T1, env=env))
        procs.append(subprocess.Popen([shutil.which("node") or "node", "--no-warnings", "src/server.ts"], cwd=T2, env={**env, "KB_URL": "http://127.0.0.1:8001"}))
        procs.append(subprocess.Popen([py, "-m", "uvicorn", "app.main:create_app", "--factory", "--port", "8003", "--log-level", "warning"], cwd=T3, env={**env, "KB_URL": "http://127.0.0.1:8001"}))
        deadline = time.time() + 60
        pending = dict(PORTS)
        while pending and time.time() < deadline:
            for name, port in list(pending.items()):
                if healthy(port):
                    pending.pop(name)
            if any(p.poll() is not None for p in procs):
                sys.exit("a service exited during startup - run it directly to see the error (see README)")
            time.sleep(0.5)
        if pending:
            sys.exit(f"timed out waiting for: {', '.join(pending)}")
        mode = "Claude" if env.get("ANTHROPIC_API_KEY") else "offline fallback (no ANTHROPIC_API_KEY)"
        print(json.dumps({"mode": mode, "knowledge_chat": "http://127.0.0.1:8001/", "inbox_review": "http://127.0.0.1:8002/", "scope_builder": "http://127.0.0.1:8003/", "api_docs": "http://127.0.0.1:8001/docs"}, indent=2))
        print("Ctrl-C to stop.")
        while all(p.poll() is None for p in procs):
            time.sleep(1)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""First-time setup on a new machine (Windows, macOS, Linux):

    python setup.py                 # check prerequisites, create .venv, install deps, create .env
    python setup.py --test          # ...and run all three test suites
    python setup.py --ingest 20     # ...and ingest N help-centre articles now (needs internet)

Afterwards start everything with:  .venv/Scripts/python start.py   (Windows)
                                   .venv/bin/python start.py       (macOS/Linux)
Idempotent: safe to run again.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
VPY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
T1, T2, T3 = ROOT / "task1-knowledge-brain", ROOT / "task2-inbox-agent", ROOT / "task3-meeting-to-scope"


def step(msg: str) -> None:
    print(f"\n==> {msg}")


def run(cmd: list[str], cwd: Path = ROOT) -> None:
    print(f"$ ({cwd.name}) {' '.join(str(c) for c in cmd)}")
    subprocess.run(cmd, cwd=cwd, check=True)


def node_version() -> tuple[int, int, int] | None:
    node = shutil.which("node")
    if not node:
        return None
    out = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
    m = re.match(r"v(\d+)\.(\d+)\.(\d+)", out)
    return tuple(int(x) for x in m.groups()) if m else None  # type: ignore[return-value]


def check_prereqs() -> None:
    step("Checking prerequisites")
    problems = []
    if sys.version_info < (3, 11):
        problems.append(f"Python 3.11+ required (found {sys.version.split()[0]}). Install from https://www.python.org/downloads/")
    nv = node_version()
    if nv is None:
        problems.append("Node.js 22.18+ required but not found. Install from https://nodejs.org/ (LTS 24 recommended)")
    elif nv < (22, 18, 0):
        problems.append(f"Node.js 22.18+ required (found {nv[0]}.{nv[1]}.{nv[2]}); older versions cannot run the TypeScript Inbox Agent directly")
    if not shutil.which("npm"):
        problems.append("npm not found (it ships with Node.js)")
    if not shutil.which("git"):
        print("note: git not found - fine for running, needed only for development")
    if problems:
        print("\n".join(f"  - {p}" for p in problems))
        sys.exit("Fix the items above and re-run python setup.py")
    print(f"  Python {sys.version.split()[0]}, Node {'.'.join(map(str, nv))} - OK")  # type: ignore[arg-type]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--test", action="store_true", help="run all three test suites after installing")
    ap.add_argument("--ingest", type=int, metavar="N", help="ingest N help-centre articles after installing")
    ap.add_argument("--check", action="store_true", help="only check prerequisites")
    a = ap.parse_args()

    check_prereqs()
    if a.check:
        return 0

    step("Creating virtual environment (.venv)")
    if not VPY.exists():
        run([sys.executable, "-m", "venv", str(VENV)])
    run([str(VPY), "-m", "pip", "install", "-q", "--upgrade", "pip"])

    step("Installing Python dependencies (Task 1 and Task 3)")
    for t in (T1, T3):
        run([str(VPY), "-m", "pip", "install", "-q", "-r", "requirements.txt"], t)

    step("Installing Node dependencies (Task 2)")
    run([shutil.which("npm") or "npm", "install", "--no-audit", "--no-fund"], T2)

    step("Creating .env")
    env, example = ROOT / ".env", ROOT / ".env.example"
    if env.exists():
        print("  .env already exists - left untouched")
    elif example.exists():
        shutil.copyfile(example, env)
        print("  created .env from .env.example. Add ANTHROPIC_API_KEY, GROQ_API_KEY and/or GEMINI_API_KEY and pick the default with LLM_PROVIDER;\n"
              "  with no key the services run in offline fallback mode.")

    if a.ingest:
        step(f"Ingesting {a.ingest} articles into the Knowledge Brain")
        run([str(VPY), "-m", "ingestion.cli", "--limit", str(a.ingest)], T1)

    if a.test:
        step("Running tests")
        run([str(VPY), "-m", "pytest", "-q"], T1)
        run([shutil.which("npm") or "npm", "test"], T2)
        run([str(VPY), "-m", "pytest", "-q"], T3)

    rel = VPY.relative_to(ROOT).as_posix()
    print(f"""
Setup complete.

Start everything:   {rel} start.py          (first run ingests 20 articles if the DB is empty)
Whole help centre:  {rel} start.py --full-ingest
Then open:          http://127.0.0.1:8001/  (Knowledge Chat)   :8002 (Inbox Review)   :8003 (Scope Builder)
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

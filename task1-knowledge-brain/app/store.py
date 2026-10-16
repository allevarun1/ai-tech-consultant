"""SQLite persistence: manifest, articles, chunks, escalations, expert records, ingestion jobs.

Why SQLite: zero-ops, one-command startup, transactional (needed for rollback of a bad
ingestion), and the corpus (hundreds of articles) fits easily in memory for vector search.
See DESIGN.md for the trade-off against pgvector.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS manifest (
  url TEXT PRIMARY KEY, lastmod TEXT, state TEXT NOT NULL,        -- pending|done|failed|removed
  attempts INTEGER NOT NULL DEFAULT 0, error TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS articles (
  url TEXT NOT NULL, version INTEGER NOT NULL, article_id TEXT NOT NULL, title TEXT, lastmod TEXT,
  content_hash TEXT NOT NULL, ingested_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, body_json TEXT NOT NULL,
  PRIMARY KEY (url, version));
CREATE TABLE IF NOT EXISTS chunks (
  chunk_id TEXT PRIMARY KEY, url TEXT NOT NULL, version INTEGER NOT NULL, title TEXT, heading TEXT,
  text TEXT NOT NULL, images_json TEXT NOT NULL DEFAULT '[]', active INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS ix_chunks_active ON chunks(active);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, state TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
  added INTEGER DEFAULT 0, updated INTEGER DEFAULT 0, removed INTEGER DEFAULT 0, unchanged INTEGER DEFAULT 0, failed INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS escalations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, question TEXT NOT NULL, reason TEXT, status TEXT NOT NULL DEFAULT 'open',
  evidence_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, resolved_at TEXT);
CREATE TABLE IF NOT EXISTS expert_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT, escalation_id INTEGER NOT NULL, version INTEGER NOT NULL DEFAULT 1,
  question TEXT NOT NULL, answer TEXT NOT NULL, evidence_urls TEXT NOT NULL DEFAULT '[]', reviewer TEXT NOT NULL,
  approved INTEGER NOT NULL DEFAULT 0, expires_at TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.executescript(SCHEMA)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Atomic block; any exception rolls back (used for ingestion rollback)."""
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    def x(self, sql: str, args: tuple = ()) -> int:
        with self._lock:
            return self._conn.execute(sql, args).lastrowid

    # -- index versioning (retrieval index rebuilds when this changes) --------
    def bump_version(self) -> None:
        self.x("INSERT INTO meta(k,v) VALUES('data_version','1') ON CONFLICT(k) DO UPDATE SET v=CAST(v AS INTEGER)+1")

    def data_version(self) -> int:
        r = self.q("SELECT v FROM meta WHERE k='data_version'")
        return int(r[0]["v"]) if r else 0

    # -- manifest -----------------------------------------------------------
    def manifest(self) -> dict[str, sqlite3.Row]:
        return {r["url"]: r for r in self.q("SELECT * FROM manifest")}

    def set_manifest(self, url: str, state: str, lastmod: Optional[str] = None, error: Optional[str] = None, bump_attempt: bool = False) -> None:
        bump = 1 if bump_attempt else 0
        self.x(
            "INSERT INTO manifest(url,lastmod,state,attempts,error,updated_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(url) DO UPDATE SET lastmod=COALESCE(excluded.lastmod,lastmod), state=excluded.state, "
            "attempts=attempts+?, error=excluded.error, updated_at=excluded.updated_at",
            (url, lastmod, state, bump, error, now(), bump),
        )

    # -- articles & chunks --------------------------------------------------
    def current_article(self, url: str) -> Optional[sqlite3.Row]:
        r = self.q("SELECT * FROM articles WHERE url=? AND active=1 ORDER BY version DESC LIMIT 1", (url,))
        return r[0] if r else None

    def save_article(self, article: dict, chunks: list[dict]) -> int:
        """Insert a new version, deactivate old ones, all in one transaction."""
        with self.tx() as c:
            prev = c.execute("SELECT COALESCE(MAX(version),0) v FROM articles WHERE url=?", (article["url"],)).fetchone()["v"]
            version = prev + 1
            c.execute("UPDATE articles SET active=0 WHERE url=?", (article["url"],))
            c.execute("UPDATE chunks SET active=0 WHERE url=?", (article["url"],))
            c.execute(
                "INSERT INTO articles(url,version,article_id,title,lastmod,content_hash,ingested_at,active,body_json) VALUES(?,?,?,?,?,?,?,1,?)",
                (article["url"], version, article["article_id"], article["title"], article.get("lastmod"), article["content_hash"], now(), json.dumps(article)),
            )
            for ch in chunks:
                c.execute(
                    "INSERT INTO chunks(chunk_id,url,version,title,heading,text,images_json,active) VALUES(?,?,?,?,?,?,?,1)",
                    (f"{article['article_id']}:v{version}:{ch['idx']}", article["url"], version, article["title"], ch["heading"], ch["text"], json.dumps(ch["images"])),
                )
        self.bump_version()
        return version

    def deactivate(self, url: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE articles SET active=0 WHERE url=?", (url,))
            c.execute("UPDATE chunks SET active=0 WHERE url=?", (url,))
        self.bump_version()

    def rollback_article(self, url: str) -> bool:
        """Re-activate the previous version of an article (undo a bad ingestion)."""
        rows = self.q("SELECT version FROM articles WHERE url=? ORDER BY version DESC LIMIT 2", (url,))
        if len(rows) < 2:
            return False
        newest, prev = rows[0]["version"], rows[1]["version"]
        with self.tx() as c:
            for t in ("articles", "chunks"):
                c.execute(f"UPDATE {t} SET active=0 WHERE url=? AND version=?", (url, newest))
                c.execute(f"UPDATE {t} SET active=1 WHERE url=? AND version=?", (url, prev))
        self.bump_version()
        return True

    def active_chunks(self) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM chunks WHERE active=1")

    def counts(self) -> dict[str, int]:
        return {
            "pages": self.q("SELECT COUNT(*) n FROM articles WHERE active=1")[0]["n"],
            "chunks": self.q("SELECT COUNT(*) n FROM chunks WHERE active=1")[0]["n"],
            "open_escalations": self.q("SELECT COUNT(*) n FROM escalations WHERE status='open'")[0]["n"],
        }

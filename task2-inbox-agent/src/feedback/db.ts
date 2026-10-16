import { DatabaseSync } from "node:sqlite";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { randomUUID } from "node:crypto";
import type { Category, Classification, Draft, EmailInput } from "../types.ts";
import { BASELINE_RULE, type StyleRule } from "../drafting/draft.ts";

const SCHEMA = `
CREATE TABLE IF NOT EXISTS emails (
  id TEXT PRIMARY KEY, from_addr TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL, received_at TEXT NOT NULL,
  thread_id TEXT, status TEXT NOT NULL, classification_json TEXT, classified_by TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS drafts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, email_id TEXT NOT NULL, version INTEGER NOT NULL, draft_json TEXT NOT NULL,
  prompt_version TEXT NOT NULL, model TEXT NOT NULL, input_tokens INTEGER, output_tokens INTEGER, latency_ms REAL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT, email_id TEXT NOT NULL, draft_id INTEGER NOT NULL, category TEXT NOT NULL, action TEXT NOT NULL,
  reviewer TEXT NOT NULL, edited_text TEXT, final_text TEXT, reason TEXT, modified INTEGER NOT NULL, qualifying INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS outbox (
  email_id TEXT PRIMARY KEY, to_addr TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL, auto INTEGER NOT NULL DEFAULT 0, simulated INTEGER NOT NULL DEFAULT 1, sent_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS category_state (
  category TEXT PRIMARY KEY, consecutive INTEGER NOT NULL DEFAULT 0, autonomy TEXT NOT NULL DEFAULT 'off', last_reason TEXT, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL, event TEXT NOT NULL, payload_json TEXT);
CREATE TABLE IF NOT EXISTS rules (version INTEGER PRIMARY KEY, text TEXT NOT NULL, directives_json TEXT NOT NULL, note TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS regression_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, rule_version INTEGER NOT NULL, metrics_json TEXT NOT NULL, passed INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT NOT NULL);
`;

export type EmailStatus = "pending_review" | "sent" | "auto_sent" | "needs_expert";
export type AutonomyState = "off" | "eligible" | "enabled" | "disabled";

export interface EmailRow {
  id: string;
  from: string;
  subject: string;
  body: string;
  receivedAt: string;
  threadId: string | null;
  status: EmailStatus;
  classification: Classification | null;
  classifiedBy: string | null;
  createdAt: string;
}
export interface ReviewRow {
  id: number;
  emailId: string;
  draftId: number;
  category: Category;
  action: string;
  reviewer: string;
  editedText: string | null;
  finalText: string | null;
  reason: string | null;
  modified: boolean;
  qualifying: boolean;
  createdAt: string;
}
export interface CategoryState {
  category: Category;
  consecutive: number;
  autonomy: AutonomyState;
  lastReason: string | null;
  updatedAt: string;
}

const now = () => new Date().toISOString();

export class InboxDb {
  readonly db: DatabaseSync;

  constructor(file: string) {
    if (file !== ":memory:") mkdirSync(path.dirname(file), { recursive: true });
    this.db = new DatabaseSync(file);
    this.db.exec("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;");
    this.db.exec(SCHEMA);
    if (!this.db.prepare("SELECT 1 FROM rules WHERE version=1").get()) {
      this.db.prepare("INSERT INTO rules(version,text,directives_json,note,created_at) VALUES(1,?,?,?,?)").run(BASELINE_RULE.text, "[]", "baseline", now());
      this.setSetting("active_rule", "1");
    }
  }

  tx<T>(fn: () => T): T {
    this.db.exec("BEGIN IMMEDIATE");
    try {
      const out = fn();
      this.db.exec("COMMIT");
      return out;
    } catch (e) {
      this.db.exec("ROLLBACK");
      throw e;
    }
  }

  // ---- emails -----------------------------------------------------------------------------
  insertEmail(e: EmailInput): { id: string; created: boolean } {
    const id = e.id ?? randomUUID().slice(0, 12);
    const r = this.db
      .prepare("INSERT OR IGNORE INTO emails(id,from_addr,subject,body,received_at,thread_id,status,created_at) VALUES(?,?,?,?,?,?,?,?)")
      .run(id, e.from, e.subject, e.body, e.receivedAt ?? now(), e.threadId ?? null, "pending_review", now());
    return { id, created: Number(r.changes) === 1 };
  }

  private toEmail(r: Record<string, unknown>): EmailRow {
    return {
      id: String(r.id),
      from: String(r.from_addr),
      subject: String(r.subject),
      body: String(r.body),
      receivedAt: String(r.received_at),
      threadId: (r.thread_id as string | null) ?? null,
      status: r.status as EmailStatus,
      classification: r.classification_json ? (JSON.parse(String(r.classification_json)) as Classification) : null,
      classifiedBy: (r.classified_by as string | null) ?? null,
      createdAt: String(r.created_at),
    };
  }
  getEmail(id: string): EmailRow | null {
    const r = this.db.prepare("SELECT * FROM emails WHERE id=?").get(id);
    return r ? this.toEmail(r) : null;
  }
  listEmails(status?: string): EmailRow[] {
    const rows = status ? this.db.prepare("SELECT * FROM emails WHERE status=? ORDER BY created_at DESC").all(status) : this.db.prepare("SELECT * FROM emails ORDER BY created_at DESC LIMIT 200").all();
    return rows.map((r) => this.toEmail(r));
  }
  setClassification(id: string, c: Classification, by: string): void {
    this.db.prepare("UPDATE emails SET classification_json=?, classified_by=? WHERE id=?").run(JSON.stringify(c), by, id);
  }
  setStatus(id: string, s: EmailStatus): void {
    this.db.prepare("UPDATE emails SET status=? WHERE id=?").run(s, id);
  }
  priorFrom(from: string, beforeId: string, limit = 3): { subject: string; summary: string }[] {
    const rows = this.db
      .prepare("SELECT subject, classification_json FROM emails WHERE from_addr=? AND id<>? AND created_at <= (SELECT created_at FROM emails WHERE id=?) ORDER BY created_at DESC LIMIT ?")
      .all(from, beforeId, beforeId, limit);
    return rows.map((r) => ({ subject: String(r.subject), summary: r.classification_json ? (JSON.parse(String(r.classification_json)) as Classification).summary : "" }));
  }

  // ---- drafts -----------------------------------------------------------------------------
  addDraft(emailId: string, d: Draft, usage?: { inputTokens: number; outputTokens: number; latencyMs: number }): { id: number; version: number } {
    const v = Number((this.db.prepare("SELECT COALESCE(MAX(version),0) v FROM drafts WHERE email_id=?").get(emailId) as { v: number }).v) + 1;
    const r = this.db
      .prepare("INSERT INTO drafts(email_id,version,draft_json,prompt_version,model,input_tokens,output_tokens,latency_ms,created_at) VALUES(?,?,?,?,?,?,?,?,?)")
      .run(emailId, v, JSON.stringify(d), d.promptVersion, d.model, usage?.inputTokens ?? null, usage?.outputTokens ?? null, usage?.latencyMs ?? null, now());
    return { id: Number(r.lastInsertRowid), version: v };
  }
  latestDraft(emailId: string): { id: number; version: number; draft: Draft } | null {
    const r = this.db.prepare("SELECT * FROM drafts WHERE email_id=? ORDER BY version DESC LIMIT 1").get(emailId);
    return r ? { id: Number(r.id), version: Number(r.version), draft: JSON.parse(String(r.draft_json)) as Draft } : null;
  }

  // ---- reviews / send ----------------------------------------------------------------------
  addReview(r: Omit<ReviewRow, "id" | "createdAt">): number {
    const res = this.db
      .prepare("INSERT INTO reviews(email_id,draft_id,category,action,reviewer,edited_text,final_text,reason,modified,qualifying,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)")
      .run(r.emailId, r.draftId, r.category, r.action, r.reviewer, r.editedText, r.finalText, r.reason, r.modified ? 1 : 0, r.qualifying ? 1 : 0, now());
    return Number(res.lastInsertRowid);
  }
  reviewsFor(category?: Category): ReviewRow[] {
    const rows = category ? this.db.prepare("SELECT * FROM reviews WHERE category=? ORDER BY id").all(category) : this.db.prepare("SELECT * FROM reviews ORDER BY id").all();
    return rows.map((r) => ({
      id: Number(r.id), emailId: String(r.email_id), draftId: Number(r.draft_id), category: r.category as Category, action: String(r.action), reviewer: String(r.reviewer),
      editedText: (r.edited_text as string | null) ?? null, finalText: (r.final_text as string | null) ?? null, reason: (r.reason as string | null) ?? null,
      modified: Number(r.modified) === 1, qualifying: Number(r.qualifying) === 1, createdAt: String(r.created_at),
    }));
  }
  /** Idempotent: a second attempt to send the same email is a no-op. */
  recordSend(emailId: string, to: string, subject: string, body: string, auto: boolean): { sent: boolean } {
    const r = this.db.prepare("INSERT OR IGNORE INTO outbox(email_id,to_addr,subject,body,auto,simulated,sent_at) VALUES(?,?,?,?,?,1,?)").run(emailId, to, subject, body, auto ? 1 : 0, now());
    return { sent: Number(r.changes) === 1 };
  }
  outbox(): { emailId: string; to: string; subject: string; body: string; auto: boolean; sentAt: string }[] {
    return this.db.prepare("SELECT * FROM outbox ORDER BY sent_at DESC").all().map((r) => ({ emailId: String(r.email_id), to: String(r.to_addr), subject: String(r.subject), body: String(r.body), auto: Number(r.auto) === 1, sentAt: String(r.sent_at) }));
  }

  // ---- category state ----------------------------------------------------------------------
  getState(category: Category): CategoryState {
    const r = this.db.prepare("SELECT * FROM category_state WHERE category=?").get(category);
    if (!r) return { category, consecutive: 0, autonomy: "off", lastReason: null, updatedAt: now() };
    return { category, consecutive: Number(r.consecutive), autonomy: r.autonomy as AutonomyState, lastReason: (r.last_reason as string | null) ?? null, updatedAt: String(r.updated_at) };
  }
  putState(s: Pick<CategoryState, "category" | "consecutive" | "autonomy" | "lastReason">): void {
    this.db
      .prepare("INSERT INTO category_state(category,consecutive,autonomy,last_reason,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(category) DO UPDATE SET consecutive=excluded.consecutive, autonomy=excluded.autonomy, last_reason=excluded.last_reason, updated_at=excluded.updated_at")
      .run(s.category, s.consecutive, s.autonomy, s.lastReason, now());
  }
  allStates(): CategoryState[] {
    return this.db.prepare("SELECT * FROM category_state").all().map((r) => ({ category: r.category as Category, consecutive: Number(r.consecutive), autonomy: r.autonomy as AutonomyState, lastReason: (r.last_reason as string | null) ?? null, updatedAt: String(r.updated_at) }));
  }

  // ---- audit & settings ----------------------------------------------------------------------
  audit(actor: string, event: string, payload: unknown = {}): void {
    this.db.prepare("INSERT INTO audit(ts,actor,event,payload_json) VALUES(?,?,?,?)").run(now(), actor, event, JSON.stringify(payload));
  }
  auditLog(limit = 100): { ts: string; actor: string; event: string; payload: unknown }[] {
    return this.db.prepare("SELECT * FROM audit ORDER BY id DESC LIMIT ?").all(limit).map((r) => ({ ts: String(r.ts), actor: String(r.actor), event: String(r.event), payload: JSON.parse(String(r.payload_json)) }));
  }
  getSetting(k: string): string | null {
    const r = this.db.prepare("SELECT v FROM settings WHERE k=?").get(k);
    return r ? String(r.v) : null;
  }
  setSetting(k: string, v: string): void {
    this.db.prepare("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v").run(k, v);
  }

  // ---- versioned style rules + regression gate -------------------------------------------------
  addRule(text: string, directives: string[], note: string): number {
    const v = Number((this.db.prepare("SELECT COALESCE(MAX(version),0) v FROM rules").get() as { v: number }).v) + 1;
    this.db.prepare("INSERT INTO rules(version,text,directives_json,note,created_at) VALUES(?,?,?,?,?)").run(v, text, JSON.stringify(directives), note, now());
    return v;
  }
  getRule(version: number): (StyleRule & { note: string | null }) | null {
    const r = this.db.prepare("SELECT * FROM rules WHERE version=?").get(version);
    return r ? { version: Number(r.version), text: String(r.text), directives: JSON.parse(String(r.directives_json)) as string[], note: (r.note as string | null) ?? null } : null;
  }
  activeRule(): StyleRule {
    return this.getRule(Number(this.getSetting("active_rule") ?? 1)) ?? BASELINE_RULE;
  }
  listRules(): (StyleRule & { note: string | null; active: boolean })[] {
    const active = Number(this.getSetting("active_rule") ?? 1);
    return this.db.prepare("SELECT version FROM rules ORDER BY version").all().map((r) => ({ ...(this.getRule(Number(r.version)) as StyleRule & { note: string | null }), active: Number(r.version) === active }));
  }
  addRegressionRun(ruleVersion: number, metrics: unknown, passed: boolean): void {
    this.db.prepare("INSERT INTO regression_runs(rule_version,metrics_json,passed,created_at) VALUES(?,?,?,?)").run(ruleVersion, JSON.stringify(metrics), passed ? 1 : 0, now());
  }
  lastRegression(ruleVersion: number): { passed: boolean; metrics: unknown } | null {
    const r = this.db.prepare("SELECT * FROM regression_runs WHERE rule_version=? ORDER BY id DESC LIMIT 1").get(ruleVersion);
    return r ? { passed: Number(r.passed) === 1, metrics: JSON.parse(String(r.metrics_json)) } : null;
  }
}

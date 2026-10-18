import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { readFileSync } from "node:fs";
import path from "node:path";
import { z, ZodError } from "zod";
import { AgentError, InboxAgent } from "./agent.ts";
import { loadConfig, type Config } from "./config.ts";
import { InboxDb } from "./feedback/db.ts";
import { analyzePatterns } from "./feedback/patterns.ts";
import { disableCategory, enableCategory, globalAutonomy } from "./feedback/autonomy.ts";
import { KnowledgeClient } from "./knowledge-client/client.ts";
import { Llm } from "./llm.ts";
import { CATEGORIES, EmailInputSchema, type Category } from "./types.ts";
import { evaluate, loadEmails } from "./evaluation/evaluate.ts";

const ReviewSchema = z.object({
  action: z.enum(["approve", "edit", "reject", "needs_expert"]),
  reviewer: z.string().min(1).max(80),
  text: z.string().max(8000).optional(),
  reason: z.string().max(500).optional(),
  overrideGuardrail: z.boolean().optional(),
});
const RuleSchema = z.object({ text: z.string().min(5).max(1000), directives: z.array(z.enum(["concise", "no_exclamation", "sign_off_short"])).default([]), note: z.string().max(300).default("") });

export function buildApp(cfg: Config = loadConfig(), parts?: { db?: InboxDb; kb?: KnowledgeClient; llm?: Llm }): { server: Server; agent: InboxAgent } {
  const db = parts?.db ?? new InboxDb(cfg.dbPath);
  const kb = parts?.kb ?? new KnowledgeClient({ baseUrl: cfg.kbUrl, apiKey: cfg.kbApiKey, timeoutMs: cfg.kbTimeoutMs });
  const llm = parts?.llm ?? new Llm(cfg);
  const agent = new InboxAgent(cfg, db, kb, llm);
  const html = readFileSync(path.join(cfg.root, "public", "index.html"), "utf-8");

  const send = (res: ServerResponse, status: number, body: unknown, headers: Record<string, string> = {}) => {
    const payload = JSON.stringify(body);
    res.writeHead(status, { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", ...headers });
    res.end(payload);
  };
  const readJson = async (req: IncomingMessage): Promise<unknown> => {
    const chunks: Buffer[] = [];
    let size = 0;
    for await (const c of req) {
      size += (c as Buffer).length;
      if (size > 200_000) throw new AgentError("request body too large", 413);
      chunks.push(c as Buffer);
    }
    try {
      return chunks.length ? JSON.parse(Buffer.concat(chunks).toString("utf-8")) : {};
    } catch {
      throw new AgentError("invalid JSON body", 400);
    }
  };
  const parseCategory = (s: string | undefined): Category => {
    if (!s || !(CATEGORIES as readonly string[]).includes(s)) throw new AgentError("unknown category", 404);
    return s as Category;
  };

  const server = createServer(async (req, res) => {
    const url = new URL(req.url ?? "/", "http://x");
    const m = (re: RegExp) => url.pathname.match(re);
    try {
      if (req.method === "GET" && (url.pathname === "/" || url.pathname === "/index.html")) {
        res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
        return void res.end(html);
      }
      if (req.method === "GET" && url.pathname === "/health") {
        const h = await kb.health();
        return send(res, 200, { status: "ok", knowledge: h, llm: llm.available ? llm.label(cfg.draftModel) : "offline", llmNotes: llm.notes, autonomyGlobal: globalAutonomy(db, cfg) });
      }
      if (req.method === "POST" && url.pathname === "/api/emails") {
        const input = EmailInputSchema.parse(await readJson(req));
        return send(res, 201, await agent.processEmail(input));
      }
      if (req.method === "GET" && url.pathname === "/api/emails") {
        const list = db.listEmails(url.searchParams.get("status") ?? undefined).map((e) => ({ ...e, draft: db.latestDraft(e.id)?.draft ?? null }));
        return send(res, 200, list);
      }
      let g: RegExpMatchArray | null;
      if (req.method === "GET" && (g = m(/^\/api\/emails\/([\w-]+)$/))) {
        const v = agent.view(g[1] as string);
        return send(res, 200, { ...v, reviews: db.reviewsFor().filter((r) => r.emailId === g![1]), outbox: db.outbox().find((o) => o.emailId === g![1]) ?? null });
      }
      if (req.method === "POST" && (g = m(/^\/api\/emails\/([\w-]+)\/review$/))) {
        return send(res, 200, agent.review(g[1] as string, ReviewSchema.parse(await readJson(req))));
      }
      if (req.method === "POST" && (g = m(/^\/api\/emails\/([\w-]+)\/qa$/))) {
        const b = z.object({ ok: z.boolean(), reviewer: z.string().min(1), note: z.string().default("") }).parse(await readJson(req));
        return send(res, 200, agent.qa(g[1] as string, b.ok, b.reviewer, b.note));
      }
      if (req.method === "GET" && url.pathname === "/api/autonomy") {
        const states = Object.fromEntries(CATEGORIES.map((c) => [c, db.getState(c)]));
        return send(res, 200, { threshold: cfg.autonomyThreshold, global: globalAutonomy(db, cfg), states });
      }
      if (req.method === "POST" && url.pathname === "/api/autonomy/global") {
        const b = z.object({ on: z.boolean(), actor: z.string().min(1) }).parse(await readJson(req));
        db.setSetting("autonomy_global", b.on ? "on" : "off");
        db.audit(b.actor, "autonomy_global", { on: b.on });
        return send(res, 200, { global: b.on });
      }
      if (req.method === "POST" && (g = m(/^\/api\/autonomy\/([a-z_]+)\/(enable|disable)$/))) {
        const cat = parseCategory(g[1]);
        const b = z.object({ actor: z.string().min(1), reason: z.string().optional() }).parse(await readJson(req));
        if (g[2] === "enable") {
          const r = enableCategory(db, cat, b.actor);
          return send(res, r.ok ? 200 : 409, { ...r, state: db.getState(cat) });
        }
        disableCategory(db, cat, b.actor, b.reason);
        return send(res, 200, { ok: true, state: db.getState(cat) });
      }
      if (req.method === "GET" && url.pathname === "/api/feedback/patterns") return send(res, 200, analyzePatterns(db));
      if (req.method === "GET" && url.pathname === "/api/audit") return send(res, 200, db.auditLog(200));
      if (req.method === "GET" && url.pathname === "/api/outbox") return send(res, 200, db.outbox());
      if (req.method === "GET" && url.pathname === "/api/rules") return send(res, 200, db.listRules());
      if (req.method === "POST" && url.pathname === "/api/rules") {
        const b = RuleSchema.parse(await readJson(req));
        const v = db.addRule(b.text, b.directives, b.note);
        db.audit("reviewer", "rule_created", { version: v });
        return send(res, 201, { version: v, activate: "run /regression first" });
      }
      if (req.method === "POST" && (g = m(/^\/api\/rules\/(\d+)\/regression$/))) {
        const v = Number(g[1]);
        const cand = db.getRule(v);
        if (!cand) throw new AgentError("rule version not found", 404);
        const fixed = loadEmails(cfg).filter((e) => e.split === "dev"); // fixed regression set; held-out test split is never used here
        const base = await evaluate(cfg, fixed, { label: "baseline", rule: db.activeRule() });
        const next = await evaluate(cfg, fixed, { label: `rule-v${v}`, rule: cand });
        const passed = next.summary.classification_accuracy >= base.summary.classification_accuracy && next.summary.unsafe_action_rate === 0 && next.summary.pass_rate >= base.summary.pass_rate;
        db.addRegressionRun(v, { baseline: base.summary, candidate: next.summary }, passed);
        return send(res, 200, { passed, baseline: base.summary, candidate: next.summary });
      }
      if (req.method === "POST" && (g = m(/^\/api\/rules\/(\d+)\/activate$/))) {
        const v = Number(g[1]);
        if (!db.getRule(v)) throw new AgentError("rule version not found", 404);
        const reg = db.lastRegression(v);
        if (v !== 1 && !reg?.passed) throw new AgentError("this rule version has not passed the regression run; refusing to activate", 409);
        db.setSetting("active_rule", String(v));
        db.audit("reviewer", "rule_activated", { version: v });
        return send(res, 200, { active: v });
      }
      throw new AgentError("not found", 404);
    } catch (e) {
      if (e instanceof ZodError) return send(res, 422, { error: "validation failed", issues: e.issues.map((i) => `${i.path.join(".")}: ${i.message}`) });
      if (e instanceof AgentError) return send(res, e.status, { error: e.message });
      console.error("unhandled", e);
      return send(res, 500, { error: "internal error" });
    }
  });
  return { server, agent };
}

if (import.meta.main) {
  const cfg = loadConfig();
  const llm = new Llm(cfg);
  const { server } = buildApp(cfg, { llm });
  server.listen(cfg.port, "127.0.0.1", () => console.log(`Inbox Agent on http://127.0.0.1:${cfg.port}  (knowledge: ${cfg.kbUrl}, ai: ${llm.available ? llm.label(cfg.draftModel) : "offline"}${llm.notes.length ? `; ${llm.notes.join("; ")}` : ""})`));
}

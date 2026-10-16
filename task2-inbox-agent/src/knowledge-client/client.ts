import { z } from "zod";
import { randomUUID } from "node:crypto";

/** Response contract of the Knowledge Brain POST /query (Task 1). Only this shape is relied upon. */
const CitationSchema = z.object({
  chunk_id: z.string(),
  url: z.string(),
  title: z.string(),
  section: z.string(),
  score: z.number(),
  excerpt: z.string(),
});
export const KbResponseSchema = z.object({
  request_id: z.string(),
  status: z.enum(["answered", "insufficient_evidence"]),
  answer: z.string().nullable().optional(),
  citations: z.array(CitationSchema),
  evidence_score: z.number(),
  reason: z.string().nullable().optional(),
  escalation_id: z.number().nullable().optional(),
  answered_by: z.string(),
});
export type KbResponse = z.infer<typeof KbResponseSchema>;

export class KnowledgeUnavailableError extends Error {
  readonly cause2: string;
  constructor(message: string, cause2 = "") {
    super(message);
    this.name = "KnowledgeUnavailableError";
    this.cause2 = cause2;
  }
}

export interface KbClientOptions {
  baseUrl: string;
  apiKey?: string;
  timeoutMs?: number;
  retries?: number;
  /** consecutive failures before the breaker opens */
  breakerThreshold?: number;
  breakerCooldownMs?: number;
  fetchImpl?: typeof fetch;
}

/** Typed client for the Knowledge Brain with timeout, bounded retries and a small circuit breaker. */
export class KnowledgeClient {
  private fails = 0;
  private openUntil = 0;
  private readonly o: Required<Omit<KbClientOptions, "fetchImpl" | "apiKey">> & { apiKey: string; fetchImpl: typeof fetch };

  constructor(opts: KbClientOptions) {
    this.o = {
      baseUrl: opts.baseUrl.replace(/\/$/, ""),
      apiKey: opts.apiKey ?? "",
      timeoutMs: opts.timeoutMs ?? 8000,
      retries: opts.retries ?? 2,
      breakerThreshold: opts.breakerThreshold ?? 4,
      breakerCooldownMs: opts.breakerCooldownMs ?? 15_000,
      fetchImpl: opts.fetchImpl ?? fetch,
    };
  }

  get breakerOpen(): boolean {
    return Date.now() < this.openUntil;
  }

  async health(): Promise<{ ok: boolean; detail: string }> {
    try {
      const r = await this.o.fetchImpl(`${this.o.baseUrl}/health`, { signal: AbortSignal.timeout(this.o.timeoutMs) });
      return { ok: r.ok, detail: r.ok ? "ok" : `HTTP ${r.status}` };
    } catch (e) {
      return { ok: false, detail: e instanceof Error ? e.message : "unreachable" };
    }
  }

  async query(question: string, opts: { context?: string; createEscalation?: boolean; requestId?: string } = {}): Promise<KbResponse> {
    if (this.breakerOpen) throw new KnowledgeUnavailableError("knowledge service circuit breaker is open", "breaker");
    const requestId = opts.requestId ?? randomUUID().slice(0, 12);
    const body = JSON.stringify({ question: question.slice(0, 2000), context: opts.context?.slice(0, 4000), create_escalation: opts.createEscalation ?? true });
    let last = "";
    for (let attempt = 0; attempt <= this.o.retries; attempt++) {
      try {
        const r = await this.o.fetchImpl(`${this.o.baseUrl}/query`, {
          method: "POST",
          headers: { "content-type": "application/json", "x-request-id": requestId, ...(this.o.apiKey ? { "x-api-key": this.o.apiKey } : {}) },
          body,
          signal: AbortSignal.timeout(this.o.timeoutMs),
        });
        if (r.status >= 400 && r.status < 500 && r.status !== 429) {
          // a client error will not fix itself: do not retry, and do not count it against the service
          throw new KnowledgeUnavailableError(`knowledge service rejected the request (HTTP ${r.status})`, `http_${r.status}`);
        }
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const parsed = KbResponseSchema.safeParse(await r.json());
        if (!parsed.success) throw new KnowledgeUnavailableError("knowledge service returned an unexpected response shape", "bad_shape");
        this.fails = 0;
        return parsed.data;
      } catch (e) {
        if (e instanceof KnowledgeUnavailableError) throw e;
        last = e instanceof Error ? e.message : String(e);
        if (attempt < this.o.retries) await new Promise((res) => setTimeout(res, 150 * 2 ** attempt));
      }
    }
    this.fails += 1;
    if (this.fails >= this.o.breakerThreshold) this.openUntil = Date.now() + this.o.breakerCooldownMs;
    throw new KnowledgeUnavailableError(`knowledge service unavailable after ${this.o.retries + 1} attempts`, last);
  }
}

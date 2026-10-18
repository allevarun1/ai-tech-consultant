import Anthropic from "@anthropic-ai/sdk";
import type { z } from "zod";
import type { Config } from "./config.ts";

/**
 * AI access: Claude, Groq and Gemini behind one `json()` call, with an ordered fallback chain.
 *   LLM_PROVIDER = claude | groq | gemini | auto | offline      (the default provider)
 *   LLM_FALLBACK = auto | none | "groq,gemini"                   (backups, tried in order)
 * Groq and Gemini are called through their OpenAI-compatible chat endpoints with plain fetch, so there is no extra
 * dependency. When no provider has a key, or every provider fails, `json()` throws and the callers use their
 * deterministic fallback (rule-based classifier, template drafts).
 */
export type ProviderName = "claude" | "groq" | "gemini";
export const PROVIDER_ORDER: readonly ProviderName[] = ["claude", "groq", "gemini"];
const KEY_ENV: Record<ProviderName, string> = { claude: "ANTHROPIC_API_KEY", groq: "GROQ_API_KEY", gemini: "GEMINI_API_KEY" };
const ENDPOINT = {
  groq: "https://api.groq.com/openai/v1/chat/completions",
  gemini: "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
} as const;

export interface LlmResult<T> {
  value: T;
  inputTokens: number;
  outputTokens: number;
  latencyMs: number;
  /** "provider:model" that actually produced the answer, e.g. "groq:llama-3.3-70b-versatile". */
  model: string;
}

interface Completion {
  text: string;
  model: string;
  inputTokens: number;
  outputTokens: number;
}
interface Provider {
  name: ProviderName;
  /** `claudeModel` is only used by the Claude provider (classify and draft use different Claude models). */
  complete(system: string, user: string, maxTokens: number, claudeModel: string): Promise<Completion>;
}

class ProviderError extends Error {}

type LlmConfig = Pick<Config, "llmProvider" | "llmFallback" | "anthropicKey" | "groqKey" | "geminiKey" | "groqModel" | "geminiModel">;

function claudeProvider(key: string): Provider {
  const client = new Anthropic({ apiKey: key, maxRetries: 1, timeout: 30_000 });
  return {
    name: "claude",
    async complete(system, user, maxTokens, model) {
      try {
        const msg = await client.messages.create({ model, max_tokens: maxTokens, system, messages: [{ role: "user", content: user }] });
        return { text: msg.content.map((b) => (b.type === "text" ? b.text : "")).join(""), model, inputTokens: msg.usage.input_tokens, outputTokens: msg.usage.output_tokens };
      } catch (e) {
        throw new ProviderError(`claude: ${e instanceof Error ? e.message.slice(0, 120) : "error"}`);
      }
    },
  };
}

function compatProvider(name: "groq" | "gemini", key: string, model: string, fetchImpl: typeof fetch): Provider {
  return {
    name,
    async complete(system, user, maxTokens) {
      let res: Response;
      try {
        res = await fetchImpl(process.env[`${name.toUpperCase()}_BASE_URL`] || ENDPOINT[name], {   // *_BASE_URL overrides the endpoint (proxy / local test server)
          method: "POST",
          headers: { "content-type": "application/json", authorization: `Bearer ${key}` },
          body: JSON.stringify({ model, max_tokens: maxTokens, temperature: 0, messages: [{ role: "system", content: system }, { role: "user", content: user }] }),
          signal: AbortSignal.timeout(30_000),
        });
      } catch (e) {
        throw new ProviderError(`${name}: network error ${e instanceof Error ? e.name : ""}`);
      }
      if (!res.ok) throw new ProviderError(`${name}: HTTP ${res.status} ${(await res.text()).slice(0, 120)}`);
      try {
        const j = (await res.json()) as { choices: { message: { content: string | null } }[]; usage?: { prompt_tokens?: number; completion_tokens?: number } };
        return { text: j.choices[0]!.message.content ?? "", model, inputTokens: j.usage?.prompt_tokens ?? 0, outputTokens: j.usage?.completion_tokens ?? 0 };
      } catch {
        throw new ProviderError(`${name}: unexpected response shape`);
      }
    },
  };
}

/** Pure selection logic (exported for tests): which providers, in which order, and why one is missing. */
export function selectProviders(cfg: Pick<LlmConfig, "llmProvider" | "llmFallback" | "anthropicKey" | "groqKey" | "geminiKey">): { names: ProviderName[]; notes: string[] } {
  const provider = (cfg.llmProvider || "auto").trim().toLowerCase();
  const fallback = (cfg.llmFallback || "auto").trim().toLowerCase();
  const keys: Record<ProviderName, string> = { claude: cfg.anthropicKey, groq: cfg.groqKey, gemini: cfg.geminiKey };
  const has = (n: ProviderName) => keys[n].trim().length > 0;
  const isName = (x: string): x is ProviderName => (PROVIDER_ORDER as readonly string[]).includes(x);
  if (provider !== "auto" && provider !== "offline" && provider !== "none" && !isName(provider)) throw new Error(`LLM_PROVIDER must be one of claude, groq, gemini, auto, offline (got '${provider}')`);
  const notes: string[] = [];
  if (provider === "offline" || provider === "none") return { names: [], notes };

  const wanted: ProviderName[] = [];
  if (provider === "auto") {
    const first = PROVIDER_ORDER.find(has);
    if (first) wanted.push(first);
    else notes.push("no API key set for claude, groq or gemini");
  } else wanted.push(provider as ProviderName);

  if (fallback === "auto") {
    for (const n of PROVIDER_ORDER) {
      if (!wanted.includes(n)) wanted.push(n);
    }
  } else if (fallback !== "none") {
    for (const n of fallback.split(",").map((x) => x.trim()).filter(Boolean)) {
      if (!isName(n)) throw new Error(`LLM_FALLBACK has unknown provider '${n}' (use claude, groq, gemini, auto or none)`);
      if (!wanted.includes(n)) wanted.push(n);
    }
  }
  const names = wanted.filter((n) => {
    if (has(n)) return true;
    if (n === provider) notes.push(`${n} was requested but ${KEY_ENV[n]} is not set`);
    return false;
  });
  return { names, notes };
}

/** JSON out, schema-validated, one repair attempt per provider, then on to the next provider. Never configured => `available` is false. */
export class Llm {
  readonly available: boolean;
  readonly notes: string[];
  readonly usage: Record<string, { calls: number; failures: number; inputTokens: number; outputTokens: number }> = {};
  lastErrors: string[] = [];
  private chain: Provider[] = [];
  private cfg: LlmConfig;

  constructor(cfg: Partial<LlmConfig>, fetchImpl: typeof fetch = fetch) {
    const full: LlmConfig = {
      llmProvider: cfg.llmProvider ?? "auto", llmFallback: cfg.llmFallback ?? "auto", anthropicKey: cfg.anthropicKey ?? "", groqKey: cfg.groqKey ?? "", geminiKey: cfg.geminiKey ?? "",
      groqModel: cfg.groqModel ?? "llama-3.3-70b-versatile", geminiModel: cfg.geminiModel ?? "gemini-2.0-flash",
    };
    this.cfg = full;
    const { names, notes } = selectProviders(full);
    this.notes = notes;
    this.chain = names.map((n) => (n === "claude" ? claudeProvider(full.anthropicKey) : compatProvider(n, n === "groq" ? full.groqKey : full.geminiKey, n === "groq" ? full.groqModel : full.geminiModel, fetchImpl)));
    this.available = this.chain.length > 0;
  }

  /** e.g. "groq:llama-3.3-70b-versatile > gemini:gemini-2.0-flash > offline" (Claude's model is chosen per call). */
  label(claudeModel = "claude"): string {
    const m = (p: Provider) => (p.name === "claude" ? claudeModel : p.name === "groq" ? this.cfg.groqModel : this.cfg.geminiModel);
    return [...this.chain.map((p) => `${p.name}:${m(p)}`), "offline"].join(" > ");
  }

  /** `model` is the Claude model to use when Claude is the provider; Groq/Gemini use their configured model. */
  async json<S extends z.ZodTypeAny>(model: string, system: string, user: string, schema: S, maxTokens = 900): Promise<LlmResult<z.infer<S>>> {
    if (!this.available) throw new Error("no AI provider configured");
    this.lastErrors = [];
    const t0 = performance.now();
    for (const p of this.chain) {
      const u = (this.usage[p.name] ??= { calls: 0, failures: 0, inputTokens: 0, outputTokens: 0 });
      let lastErr = "";
      let inT = 0;
      let outT = 0;
      let ok: LlmResult<z.infer<S>> | null = null;
      for (let attempt = 0; attempt < 2 && !ok; attempt++) {
        let c: Completion;
        try {
          c = await p.complete(system, attempt === 0 ? user : `${user}\n\nYour previous reply was invalid (${lastErr}). Reply with ONLY valid JSON matching the schema.`, maxTokens, model);
        } catch (e) {
          lastErr = e instanceof Error ? e.message : "provider error";
          break; // provider-level failure (rate limit, outage, bad key): no point repairing, move to the next provider
        }
        inT += c.inputTokens;
        outT += c.outputTokens;
        const m = c.text.match(/\{[\s\S]*\}/);
        try {
          ok = { value: schema.parse(JSON.parse(m ? m[0] : c.text)), inputTokens: inT, outputTokens: outT, latencyMs: performance.now() - t0, model: `${p.name}:${c.model}` };
        } catch (e) {
          lastErr = `invalid JSON: ${e instanceof Error ? e.message.slice(0, 120) : "parse error"}`;
        }
      }
      u.inputTokens += inT;
      u.outputTokens += outT;
      if (ok) {
        u.calls += 1;
        return ok;
      }
      u.failures += 1;
      this.lastErrors.push(`${p.name}: ${lastErr}`);
    }
    throw new Error(`every AI provider failed: ${this.lastErrors.join("; ")}`);
  }
}

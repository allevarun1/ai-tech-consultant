import Anthropic from "@anthropic-ai/sdk";
import type { z } from "zod";
import type { Config } from "./config.ts";

export interface LlmResult<T> {
  value: T;
  inputTokens: number;
  outputTokens: number;
  latencyMs: number;
  model: string;
}

/** Thin wrapper: JSON out, schema-validated, one repair retry. Null when no API key is configured. */
export class Llm {
  readonly available: boolean;
  private client: Anthropic | null;

  constructor(cfg: Pick<Config, "anthropicKey">) {
    this.available = cfg.anthropicKey.length > 0;
    this.client = this.available ? new Anthropic({ apiKey: cfg.anthropicKey, maxRetries: 2, timeout: 30_000 }) : null;
  }

  async json<S extends z.ZodTypeAny>(model: string, system: string, user: string, schema: S, maxTokens = 900): Promise<LlmResult<z.infer<S>>> {
    if (!this.client) throw new Error("LLM not configured");
    const t0 = performance.now();
    let inT = 0;
    let outT = 0;
    let lastErr = "";
    for (let attempt = 0; attempt < 2; attempt++) {
      const msg = await this.client.messages.create({
        model,
        max_tokens: maxTokens,
        system,
        messages: [{ role: "user", content: attempt === 0 ? user : `${user}\n\nYour previous reply was invalid (${lastErr}). Reply with ONLY valid JSON matching the schema.` }],
      });
      inT += msg.usage.input_tokens;
      outT += msg.usage.output_tokens;
      const text = msg.content.map((b) => (b.type === "text" ? b.text : "")).join("");
      const m = text.match(/\{[\s\S]*\}/);
      try {
        const parsed = schema.parse(JSON.parse(m ? m[0] : text));
        return { value: parsed, inputTokens: inT, outputTokens: outT, latencyMs: performance.now() - t0, model };
      } catch (e) {
        lastErr = e instanceof Error ? e.message.slice(0, 200) : "parse error";
      }
    }
    throw new Error(`model returned invalid JSON twice: ${lastErr}`);
  }
}

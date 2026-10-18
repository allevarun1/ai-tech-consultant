/**
 * node src/evaluation/run-eval.ts [--split dev|test|all] [--offline] [--compare claude:modelA,groq:modelB,...]
 *
 * Needs the Knowledge Brain running (KB_URL, default http://127.0.0.1:8001).
 * Also runs three "knowledge service down" cases to measure the failure path.
 * Writes results/inbox_<label>.csv and results/summary.json. Nothing is pre-filled.
 */
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { loadConfig } from "../config.ts";
import { evaluate, loadEmails, toCsv, type EvalEmail, type EvalRow } from "./evaluate.ts";
import { KnowledgeClient } from "../knowledge-client/client.ts";
import { Llm } from "../llm.ts";

const args = process.argv.slice(2);
const flag = (n: string) => (args.includes(`--${n}`) ? (args[args.indexOf(`--${n}`) + 1] ?? "") : null);
const cfg = loadConfig();
const split = flag("split") ?? "all";
const offline = args.includes("--offline");
const compare = flag("compare")?.split(",").filter(Boolean) ?? [];

const health = await new KnowledgeClient({ baseUrl: cfg.kbUrl, apiKey: cfg.kbApiKey }).health();
if (!health.ok) {
  console.error(`Knowledge Brain not reachable at ${cfg.kbUrl} (${health.detail}). Start it first (see README) - refusing to report misleading results.`);
  process.exit(2);
}

const all = loadEmails(cfg);
const emails = all.filter((e) => split === "all" || e.split === split);
const outDir = path.join(cfg.root, "src", "evaluation", "results");
mkdirSync(outDir, { recursive: true });

// Reliability cases: same how-to questions with the knowledge service unreachable.
const downCases: EvalEmail[] = all
  .filter((e) => e.behavior === "grounded")
  .slice(0, 3)
  .map((e) => ({ ...e, id: `${e.id}-KBDOWN`, behavior: "escalate" as const }));

// A comparison entry is "provider:model" (claude:..., groq:..., gemini:...); a plain model name means Claude.
const parseSpec = (spec: string) => {
  const i = spec.indexOf(":");
  const provider = i > 0 ? spec.slice(0, i) : "claude";
  const model = i > 0 ? spec.slice(i + 1) : spec;
  if (!["claude", "groq", "gemini"].includes(provider) || !model) throw new Error(`--compare entries must look like claude:<model>, groq:<model> or gemini:<model> (got '${spec}')`);
  return { label: `${provider}:${model}`, provider, model, noLlm: false };
};
const ai = new Llm(cfg);
const configs: { label: string; provider?: string; model?: string; noLlm: boolean }[] = offline || !ai.available
  ? [{ label: "offline-rules+templates", noLlm: true }]
  : compare.length
    ? compare.map(parseSpec)
    : [{ label: ai.label(cfg.draftModel), noLlm: false }];
if (!offline && !ai.available) console.warn(`No AI provider available (${ai.notes.join("; ") || "none configured"}): running the offline baseline.`);

const summaries = [];
for (const c of configs) {
  const main = await evaluate(cfg, emails, { ...c, rule: undefined });
  const down = await evaluate(cfg, downCases, { ...c, kbUrl: "http://127.0.0.1:9" });
  const rows: EvalRow[] = [...main.rows, ...down.rows.map((r) => ({ ...r, case_id: r.case_id }))];
  writeFileSync(path.join(outDir, `inbox_${c.label.replace(/[^a-z0-9.+-]/gi, "_")}.csv`), toCsv(rows));
  const heldOut = main.rows.filter((r) => r.split === "test");
  summaries.push({
    ...main.summary,
    held_out_test_cases: heldOut.length,
    held_out_pass_rate: heldOut.length ? Math.round((heldOut.filter((r) => r.passed).length / heldOut.length) * 1000) / 1000 : null,
    kb_down_cases: down.rows.length,
    kb_down_pass_rate: down.summary.pass_rate,
    split,
  });
}
writeFileSync(path.join(outDir, "summary.json"), JSON.stringify(summaries, null, 2));
console.log(JSON.stringify(summaries, null, 2));

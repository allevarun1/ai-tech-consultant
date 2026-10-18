import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

export interface Config {
  port: number;
  dbPath: string;
  kbUrl: string;
  kbApiKey: string;
  kbTimeoutMs: number;
  /** AI provider: claude | groq | gemini | auto | offline (see src/llm.ts) */
  llmProvider: string;
  /** backups: auto | none | comma list such as "groq,gemini" */
  llmFallback: string;
  anthropicKey: string;
  groqKey: string;
  geminiKey: string;
  classifyModel: string;
  draftModel: string;
  /** free-tier provider models; model names change, so check the provider's current list */
  groqModel: string;
  geminiModel: string;
  companyName: string;
  supportSignature: string;
  autonomyThreshold: number;
  /** Global kill switch for automatic sending. Off unless explicitly turned on. */
  autonomyGlobalDefault: boolean;
  root: string;
}

/** Load KEY=VALUE lines from the repo-root .env into process.env. Variables that are already set win. */
export function loadDotenv(file: string, env: NodeJS.ProcessEnv = process.env): void {
  let text: string;
  try {
    text = readFileSync(file, "utf-8").replace(/^﻿/, "");
  } catch {
    return;
  }
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    const eq = line.indexOf("=");
    if (!line || line.startsWith("#") || eq < 1) continue;
    const key = line.slice(0, eq).trim();
    let value = line.slice(eq + 1).trim();
    if (value.length >= 2 && (value[0] === '"' || value[0] === "'") && value.endsWith(value[0]!)) value = value.slice(1, -1);
    else {
      value = value.split(/\s#/)[0]!.trim();
      if (value.startsWith("#")) value = "";
    }
    if (key && value && env[key] === undefined) env[key] = value;
  }
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  if (env === process.env) loadDotenv(path.join(root, "..", ".env"));
  return {
    port: Number(env.INBOX_PORT ?? 8002),
    dbPath: env.INBOX_DB_PATH ?? path.join(root, "data", "inbox.sqlite"),
    kbUrl: (env.KB_URL ?? "http://127.0.0.1:8001").replace(/\/$/, ""),
    kbApiKey: env.KB_API_KEY ?? "",
    kbTimeoutMs: Number(env.KB_TIMEOUT_MS ?? 8000),
    llmProvider: env.LLM_PROVIDER ?? "auto",
    llmFallback: env.LLM_FALLBACK ?? "auto",
    anthropicKey: env.ANTHROPIC_API_KEY ?? "",
    groqKey: env.GROQ_API_KEY ?? "",
    geminiKey: env.GEMINI_API_KEY ?? "",
    classifyModel: env.INBOX_CLASSIFY_MODEL ?? "claude-haiku-5-5",
    draftModel: env.INBOX_DRAFT_MODEL ?? "claude-sonnet-5-5",
    groqModel: env.GROQ_MODEL ?? "llama-3.3-70b-versatile",
    geminiModel: env.GEMINI_MODEL ?? "gemini-2.0-flash",
    companyName: env.COMPANY_NAME ?? "Northwind Web Studio",
    supportSignature: env.SUPPORT_SIGNATURE ?? "The Northwind Support Team",
    autonomyThreshold: Number(env.AUTONOMY_THRESHOLD ?? 20),
    autonomyGlobalDefault: env.AUTONOMY_GLOBAL === "on",
    root,
  };
}

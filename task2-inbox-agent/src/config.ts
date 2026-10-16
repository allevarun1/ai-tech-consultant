import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

export interface Config {
  port: number;
  dbPath: string;
  kbUrl: string;
  kbApiKey: string;
  kbTimeoutMs: number;
  anthropicKey: string;
  classifyModel: string;
  draftModel: string;
  companyName: string;
  supportSignature: string;
  autonomyThreshold: number;
  /** Global kill switch for automatic sending. Off unless explicitly turned on. */
  autonomyGlobalDefault: boolean;
  root: string;
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  return {
    port: Number(env.INBOX_PORT ?? 8002),
    dbPath: env.INBOX_DB_PATH ?? path.join(root, "data", "inbox.sqlite"),
    kbUrl: (env.KB_URL ?? "http://127.0.0.1:8001").replace(/\/$/, ""),
    kbApiKey: env.KB_API_KEY ?? "",
    kbTimeoutMs: Number(env.KB_TIMEOUT_MS ?? 8000),
    anthropicKey: env.ANTHROPIC_API_KEY ?? "",
    classifyModel: env.INBOX_CLASSIFY_MODEL ?? "claude-haiku-5-5",
    draftModel: env.INBOX_DRAFT_MODEL ?? "claude-sonnet-5-5",
    companyName: env.COMPANY_NAME ?? "Northwind Web Studio",
    supportSignature: env.SUPPORT_SIGNATURE ?? "The Northwind Support Team",
    autonomyThreshold: Number(env.AUTONOMY_THRESHOLD ?? 20),
    autonomyGlobalDefault: env.AUTONOMY_GLOBAL === "on",
    root,
  };
}

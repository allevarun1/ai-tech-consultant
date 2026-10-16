import type { Config } from "../config.ts";
import type { Category, Classification, Draft } from "../types.ts";
import { RESTRICTED_CATEGORIES } from "../types.ts";
import type { CategoryState, InboxDb } from "./db.ts";

/**
 * The "20 consecutive unmodified drafts" rule - defined precisely so it is auditable.
 *
 *  "Same kind"   = the classifier's category (one counter per category).
 *  "Unmodified"  = the text actually sent equals the generated draft after collapsing whitespace.
 *                  Case and punctuation DO matter. Clicking "approve" only counts if nothing changed.
 *  Qualifying    = approved/edited-and-sent, unmodified, and the draft passed the guardrails with no triggered fallback.
 *  Reset         = any edit, rejection, expert hand-off or failed quality check sets the counter back to 0.
 *
 * Reaching the threshold only makes a category ELIGIBLE. A human must switch it on (a "controlled trial"), a global
 * kill switch must be on, and every individual email must still be low-risk, unflagged and fully grounded.
 */
export const norm = (s: string): string => s.replace(/\s+/g, " ").trim();
export const isUnmodified = (draftText: string, finalText: string): boolean => norm(draftText) === norm(finalText);

const MONITOR_WINDOW = 10;
const MAX_QA_FAILS_IN_WINDOW = 1; // a second failed QA check inside the window disables the category

export function applyReview(db: InboxDb, cfg: Config, category: Category, qualifying: boolean, note: string): CategoryState {
  const st = db.getState(category);
  const consecutive = qualifying ? st.consecutive + 1 : 0;
  let autonomy = st.autonomy;
  let reason = st.lastReason;

  if (RESTRICTED_CATEGORIES.includes(category)) {
    autonomy = "off";
    reason = "restricted category: always requires human review";
  } else if (autonomy === "enabled" && !qualifying) {
    autonomy = "disabled";
    reason = `disabled automatically: ${note}`;
    db.audit("system", "autonomy_disabled", { category, note });
  } else if ((autonomy === "off" || autonomy === "disabled") && consecutive >= cfg.autonomyThreshold) {
    autonomy = "eligible";
    reason = `${consecutive} consecutive unmodified drafts`;
    db.audit("system", "autonomy_eligible", { category, consecutive });
  }
  db.putState({ category, consecutive, autonomy, lastReason: reason });
  return db.getState(category);
}

/** Post-send quality check on an auto-sent email. A failure disables the category immediately. */
export function applyQa(db: InboxDb, category: Category, ok: boolean, actor: string, note: string): CategoryState {
  const st = db.getState(category);
  if (!ok && st.autonomy === "enabled") {
    db.putState({ category, consecutive: 0, autonomy: "disabled", lastReason: `QA failed by ${actor}: ${note}` });
    db.audit(actor, "autonomy_disabled", { category, note, via: "qa_fail" });
  } else if (!ok) {
    db.putState({ category, consecutive: 0, autonomy: st.autonomy, lastReason: st.lastReason });
  } else {
    const recent = db.reviewsFor(category).filter((r) => r.action === "qa_fail").slice(-MONITOR_WINDOW);
    if (recent.length > MAX_QA_FAILS_IN_WINDOW && st.autonomy === "enabled") {
      db.putState({ category, consecutive: 0, autonomy: "disabled", lastReason: "too many QA failures in the monitoring window" });
    }
  }
  return db.getState(category);
}

export function enableCategory(db: InboxDb, category: Category, actor: string): { ok: boolean; reason: string } {
  const st = db.getState(category);
  if (RESTRICTED_CATEGORIES.includes(category)) return { ok: false, reason: "restricted category" };
  if (st.autonomy !== "eligible") return { ok: false, reason: `category is '${st.autonomy}', not 'eligible' (${st.consecutive} consecutive)` };
  db.putState({ category, consecutive: st.consecutive, autonomy: "enabled", lastReason: `enabled by ${actor}` });
  db.audit(actor, "autonomy_enabled", { category });
  return { ok: true, reason: "enabled" };
}

export function disableCategory(db: InboxDb, category: Category, actor: string, why = "manual"): void {
  const st = db.getState(category);
  db.putState({ category, consecutive: 0, autonomy: "disabled", lastReason: `disabled by ${actor}: ${why}` });
  db.audit(actor, "autonomy_disabled", { category, why });
}

export function globalAutonomy(db: InboxDb, cfg: Config): boolean {
  const v = db.getSetting("autonomy_global");
  return v === null ? cfg.autonomyGlobalDefault : v === "on";
}

export function canAutoSend(db: InboxDb, cfg: Config, cls: Classification, draft: Draft): { ok: boolean; reason: string } {
  if (!globalAutonomy(db, cfg)) return { ok: false, reason: "global autonomy switch is off" };
  if (RESTRICTED_CATEGORIES.includes(cls.category)) return { ok: false, reason: "restricted category" };
  if (db.getState(cls.category).autonomy !== "enabled") return { ok: false, reason: "category not enabled for autonomy" };
  if (cls.risk !== "low") return { ok: false, reason: `risk is ${cls.risk}` };
  if (cls.flags.length > 0) return { ok: false, reason: `flags present: ${cls.flags.join(",")}` };
  if (draft.knowledgeStatus !== "answered" || draft.draftKind !== "grounded") return { ok: false, reason: "reply is not fully grounded in knowledge" };
  if (draft.guardrailTriggered) return { ok: false, reason: "a guardrail fired on this draft" };
  return { ok: true, reason: "all autonomy conditions met" };
}

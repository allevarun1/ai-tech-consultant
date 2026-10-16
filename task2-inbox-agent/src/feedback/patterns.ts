import type { Category } from "../types.ts";
import { CATEGORIES } from "../types.ts";
import type { InboxDb } from "./db.ts";
import { norm } from "./autonomy.ts";

export interface CategoryPattern {
  category: Category;
  reviews: number;
  editRate: number;
  rejectRate: number;
  avgLengthRatio: number | null; // final / draft; <1 means reviewers shorten replies
  topRemoved: { text: string; count: number }[];
  topAdded: { text: string; count: number }[];
  topReasons: { reason: string; count: number }[];
  suggestions: string[];
}

const sentences = (t: string) => t.split(/(?<=[.!?])\s+|\n+/).map(norm).filter((s) => s.length > 12);
const top = (m: Map<string, number>, n = 3, min = 2) => [...m.entries()].filter(([, c]) => c >= min).sort((a, b) => b[1] - a[1]).slice(0, n).map(([text, count]) => ({ text, count }));

/** Turn raw human edits into recurring, reviewable patterns. Suggestions only appear when a pattern repeats (>=2). */
export function analyzePatterns(db: InboxDb): CategoryPattern[] {
  const out: CategoryPattern[] = [];
  for (const category of CATEGORIES) {
    const rs = db.reviewsFor(category).filter((r) => ["approve", "edit", "reject"].includes(r.action));
    if (!rs.length) continue;
    const removed = new Map<string, number>();
    const added = new Map<string, number>();
    const reasons = new Map<string, number>();
    const ratios: number[] = [];
    for (const r of rs) {
      if (r.reason) reasons.set(r.reason.toLowerCase().trim(), (reasons.get(r.reason.toLowerCase().trim()) ?? 0) + 1);
      if (!r.modified || !r.finalText) continue;
      const d = db.latestDraft(r.emailId);
      if (!d) continue;
      ratios.push(norm(r.finalText).length / Math.max(1, norm(d.draft.suggestedReply).length));
      const ds = new Set(sentences(d.draft.suggestedReply));
      const fs = new Set(sentences(r.finalText));
      for (const s of ds) if (!fs.has(s)) removed.set(s, (removed.get(s) ?? 0) + 1);
      for (const s of fs) if (!ds.has(s)) added.set(s, (added.get(s) ?? 0) + 1);
    }
    const avg = ratios.length ? ratios.reduce((a, b) => a + b, 0) / ratios.length : null;
    const topRemoved = top(removed);
    const topAdded = top(added);
    const suggestions: string[] = [];
    if (avg !== null && avg < 0.85 && ratios.length >= 3) suggestions.push(`Reviewers shorten ${category} replies to ~${Math.round(avg * 100)}% of the draft: consider the 'concise' directive.`);
    for (const r of topRemoved) suggestions.push(`Sentence removed ${r.count}x in ${category}: "${r.text.slice(0, 90)}" - consider dropping it from the template.`);
    for (const a of topAdded) suggestions.push(`Sentence added ${a.count}x in ${category}: "${a.text.slice(0, 90)}" - consider adding an approved response pattern.`);
    out.push({
      category,
      reviews: rs.length,
      editRate: rs.filter((r) => r.modified && r.action !== "reject").length / rs.length,
      rejectRate: rs.filter((r) => r.action === "reject").length / rs.length,
      avgLengthRatio: avg,
      topRemoved,
      topAdded,
      topReasons: [...reasons.entries()].sort((a, b) => b[1] - a[1]).slice(0, 3).map(([reason, count]) => ({ reason, count })),
      suggestions,
    });
  }
  return out;
}

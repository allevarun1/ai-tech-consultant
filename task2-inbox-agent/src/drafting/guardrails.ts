import type { Category } from "../types.ts";

/**
 * Deterministic checks applied to EVERY draft, whoever wrote it.
 * Returns a reason string when the reply makes a commitment the agent is not authorised to make, else null.
 */
const COMMITMENTS: { re: RegExp; why: string; categories?: readonly Category[] }[] = [
  { re: /\b(we|i)('ll| will| can| are going to)\s+(issue|process|give|send)?\s*(you )?(a |the )?(full |partial )?(refund|credit|reimbursement)/i, why: "promises a refund or credit" },
  { re: /\b(refund|credit)\b[^.]{0,40}\b(has been|is|was|will be)\s+(approved|issued|processed|granted)/i, why: "states a refund outcome" },
  { re: /\b(waive|waived|waiving)\b/i, why: "offers to waive a fee" },
  { re: /(?:[$€£₹]\s?\d[\d,]*(?:\.\d+)?|\b\d[\d,]*(?:\.\d+)?\s?(?:usd|eur|gbp|inr|dollars|euros|rupees)\b)/i, why: "quotes a price", categories: ["quote_request", "project_request", "refund_cancellation", "complaint", "follow_up"] },
  { re: /\b(will|should)\s+be\s+(ready|done|delivered|live|completed)\s+(by|in|within|on)\b/i, why: "promises a delivery date", categories: ["quote_request", "project_request", "complaint", "follow_up"] },
  { re: /\bguarantee[ds]?\b/i, why: "gives a guarantee" },
  { re: /\b(it'?s|that'?s|this is) (our|my) fault\b|\bwe (admit|accept) (liability|responsibility for the loss)\b/i, why: "admits liability", categories: ["complaint", "refund_cancellation"] },
  { re: /\b(legally|under the law|per our contract you are entitled)\b/i, why: "gives legal advice", categories: ["complaint", "refund_cancellation"] },
];

export function checkReply(text: string, category: Category): string | null {
  for (const c of COMMITMENTS) {
    if (c.categories && !c.categories.includes(category)) continue;
    if (c.re.test(text)) return c.why;
  }
  return null;
}

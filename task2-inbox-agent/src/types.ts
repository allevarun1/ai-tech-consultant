import { z } from "zod";

export const CATEGORIES = [
  "how_to",
  "troubleshooting",
  "project_request",
  "refund_cancellation",
  "complaint",
  "quote_request",
  "follow_up",
] as const;
export type Category = (typeof CATEGORIES)[number];

export const RISKS = ["low", "moderate", "high"] as const;
export type Risk = (typeof RISKS)[number];

export const FLAGS = ["security", "legal", "financial", "churn_risk", "injection_attempt", "escalate_to_expert"] as const;
export type Flag = (typeof FLAGS)[number];

export const EmailInputSchema = z.object({
  id: z.string().min(1).max(64).optional(),
  from: z.string().email().max(200),
  subject: z.string().min(1).max(300),
  body: z.string().min(1).max(20000),
  receivedAt: z.string().optional(),
  threadId: z.string().max(100).optional(),
});
export type EmailInput = z.infer<typeof EmailInputSchema>;

export const ClassificationSchema = z.object({
  category: z.enum(CATEGORIES),
  urgency: z.enum(["low", "normal", "high"]),
  risk: z.enum(RISKS),
  summary: z.string().min(3).max(400),
  flags: z.array(z.enum(FLAGS)).default([]),
});
export type Classification = z.infer<typeof ClassificationSchema>;

export const EvidenceSchema = z.object({
  url: z.string(),
  title: z.string(),
  chunkId: z.string(),
  score: z.number(),
  excerpt: z.string().optional(),
});
export type Evidence = z.infer<typeof EvidenceSchema>;

export const KNOWLEDGE_STATUS = ["answered", "insufficient_evidence", "unavailable", "not_needed"] as const;
export type KnowledgeStatus = (typeof KNOWLEDGE_STATUS)[number];

/** What the reviewer sees. Every field is validated before it is stored. */
export const DraftSchema = z.object({
  category: z.enum(CATEGORIES),
  summary: z.string().min(3),
  suggestedReply: z.string().min(10).max(6000),
  evidence: z.array(EvidenceSchema),
  risk: z.enum(RISKS),
  requiresHumanApproval: z.boolean(),
  nextAction: z.string().nullable(),
  followUpDate: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).nullable(),
  knowledgeStatus: z.enum(KNOWLEDGE_STATUS),
  draftKind: z.enum(["grounded", "policy_template", "holding"]),
  escalationId: z.number().nullable().default(null),
  guardrailTriggered: z.string().nullable().default(null),
  promptVersion: z.string(),
  model: z.string(),
});
export type Draft = z.infer<typeof DraftSchema>;

/** The slice of the model's output we ask for when drafting with Claude. */
export const LlmDraftSchema = z.object({
  suggestedReply: z.string().min(10).max(6000),
  nextAction: z.string().max(300).nullable(),
  followUpDate: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).nullable(),
});
export type LlmDraft = z.infer<typeof LlmDraftSchema>;

export const RESTRICTED_CATEGORIES: readonly Category[] = ["refund_cancellation", "complaint"];

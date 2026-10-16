import type { Config } from "./config.ts";
import type { Llm } from "./llm.ts";
import type { KnowledgeClient, KbResponse } from "./knowledge-client/client.ts";
import { KnowledgeUnavailableError } from "./knowledge-client/client.ts";
import type { Category, Classification, Draft, EmailInput } from "./types.ts";
import { classify } from "./email/classify.ts";
import { buildDraft, type StyleRule } from "./drafting/draft.ts";
import { checkReply } from "./drafting/guardrails.ts";
import type { EmailRow, InboxDb } from "./feedback/db.ts";
import { applyQa, applyReview, canAutoSend, isUnmodified } from "./feedback/autonomy.ts";

/** Categories where product documentation is relevant evidence. Complaints and follow-ups depend on the customer's history instead. */
const KB_CATEGORIES: readonly Category[] = ["how_to", "troubleshooting", "refund_cancellation", "project_request", "quote_request"];
/** Only genuine product questions should open a human-expert task in the Knowledge Brain. */
const ESCALATING_CATEGORIES: readonly Category[] = ["how_to", "troubleshooting"];

export type ReviewAction = "approve" | "edit" | "reject" | "needs_expert";
export interface ReviewRequest {
  action: ReviewAction;
  reviewer: string;
  text?: string;
  reason?: string;
  overrideGuardrail?: boolean;
}

export class AgentError extends Error {
  readonly status: number;
  constructor(message: string, status = 400) {
    super(message);
    this.name = "AgentError";
    this.status = status;
  }
}

export interface EmailView {
  email: EmailRow;
  draft: { id: number; version: number; draft: Draft } | null;
  autoSent: boolean;
  autonomyDecision: string;
  usage?: { classifyTokens?: { in: number; out: number }; draftTokens?: { in: number; out: number }; latencyMs: number };
}

export class InboxAgent {
  readonly cfg: Config;
  readonly db: InboxDb;
  readonly kb: KnowledgeClient;
  readonly llm: Llm;
  /** Evaluation can pin a style rule without touching the active one. */
  ruleOverride: StyleRule | null = null;

  constructor(cfg: Config, db: InboxDb, kb: KnowledgeClient, llm: Llm) {
    this.cfg = cfg;
    this.db = db;
    this.kb = kb;
    this.llm = llm;
  }

  private rule(): StyleRule {
    return this.ruleOverride ?? this.db.activeRule();
  }

  async processEmail(input: EmailInput): Promise<EmailView> {
    const t0 = performance.now();
    const { id, created } = this.db.insertEmail(input);
    if (!created) return this.view(id); // idempotent ingest: same id twice does not create a second draft

    const email = this.db.getEmail(id) as EmailRow;
    const c = await classify(input, this.llm, this.cfg.classifyModel);
    this.db.setClassification(id, c.classification, c.by);
    const cls: Classification = c.classification;
    this.db.audit("agent", "classified", { id, category: cls.category, risk: cls.risk, flags: cls.flags, by: c.by });

    let kb: KbResponse | null = null;
    let kbError: string | null = null;
    if (KB_CATEGORIES.includes(cls.category)) {
      // If the email contains an injection attempt, query with the subject only: the body is untrusted.
      const question = cls.flags.includes("injection_attempt") ? input.subject : `${input.subject}. ${input.body}`.slice(0, 1500);
      try {
        kb = await this.kb.query(question, { createEscalation: ESCALATING_CATEGORIES.includes(cls.category), requestId: id.slice(0, 12) });
      } catch (e) {
        kbError = e instanceof KnowledgeUnavailableError ? e.message : "unexpected knowledge client error";
        this.db.audit("agent", "knowledge_unavailable", { id, error: kbError });
      }
    }

    const { draft, usage } = await buildDraft({ email: input, cls, kb, kbError, prior: this.db.priorFrom(input.from, id), rule: this.rule() }, this.cfg, this.llm);
    const saved = this.db.addDraft(id, draft, usage);
    this.db.audit("agent", "draft_created", { id, draftId: saved.id, kind: draft.draftKind, knowledge: draft.knowledgeStatus, guardrail: draft.guardrailTriggered, promptVersion: draft.promptVersion });

    const decision = canAutoSend(this.db, this.cfg, cls, draft);
    let autoSent = false;
    if (decision.ok) {
      const r = this.db.recordSend(id, email.from, `Re: ${email.subject}`, draft.suggestedReply, true);
      autoSent = r.sent;
      this.db.setStatus(id, "auto_sent");
      this.db.audit("system", "auto_sent", { id, category: cls.category });
    }
    const v = this.view(id);
    v.autoSent = autoSent;
    v.autonomyDecision = decision.reason;
    v.usage = { classifyTokens: c.tokens, draftTokens: usage ? { in: usage.inputTokens, out: usage.outputTokens } : undefined, latencyMs: performance.now() - t0 };
    return v;
  }

  view(id: string): EmailView {
    const email = this.db.getEmail(id);
    if (!email) throw new AgentError("email not found", 404);
    return { email, draft: this.db.latestDraft(id), autoSent: email.status === "auto_sent", autonomyDecision: "" };
  }

  review(emailId: string, req: ReviewRequest): { status: string; modified: boolean; qualifying: boolean; categoryState: ReturnType<InboxDb["getState"]>; alreadySent: boolean } {
    const email = this.db.getEmail(emailId);
    if (!email) throw new AgentError("email not found", 404);
    if (email.status === "sent" || email.status === "auto_sent") throw new AgentError("this email has already been sent", 409);
    const d = this.db.latestDraft(emailId);
    if (!d || !email.classification) throw new AgentError("email has no draft yet", 409);
    const category = email.classification.category;
    const draftText = d.draft.suggestedReply;

    let finalText: string | null = null;
    let modified = false;
    if (req.action === "approve") {
      finalText = draftText;
    } else if (req.action === "edit") {
      if (!req.text || req.text.trim().length < 10) throw new AgentError("edited text is required");
      finalText = req.text;
      modified = !isUnmodified(draftText, finalText);
      if (modified && !req.reason?.trim()) throw new AgentError("a reason is required when you change a draft");
    } else if (req.action === "reject") {
      if (!req.text || req.text.trim().length < 10) throw new AgentError("a replacement reply is required when rejecting");
      if (!req.reason?.trim()) throw new AgentError("a reason is required when rejecting");
      finalText = req.text;
      modified = true;
    } else if (req.action === "needs_expert") {
      if (!req.reason?.trim()) throw new AgentError("say what the expert needs to look at");
    }

    if (finalText !== null) {
      const violation = checkReply(finalText, category);
      if (violation && !req.overrideGuardrail) throw new AgentError(`blocked: the reply ${violation}. Edit it, or resubmit with overrideGuardrail and a reason.`, 422);
      if (violation && req.overrideGuardrail && !req.reason?.trim()) throw new AgentError("overriding a guardrail needs a reason", 422);
      if (violation) this.db.audit(req.reviewer, "guardrail_override", { emailId, violation, reason: req.reason });
    }

    // Qualifying = counts toward the consecutive-unmodified streak (see feedback/autonomy.ts).
    const qualifying = req.action === "approve" || req.action === "edit" ? !modified && d.draft.guardrailTriggered === null && finalText !== null : false;
    const note = req.action === "needs_expert" ? "sent to expert" : modified ? "reviewer changed the draft" : "draft guardrail fired";

    return this.db.tx(() => {
      this.db.addReview({
        emailId, draftId: d.id, category, action: req.action, reviewer: req.reviewer, editedText: req.text ?? null, finalText, reason: req.reason ?? null, modified, qualifying,
      });
      let alreadySent = false;
      if (finalText !== null) {
        const r = this.db.recordSend(emailId, email.from, `Re: ${email.subject}`, finalText, false);
        alreadySent = !r.sent;
        this.db.setStatus(emailId, "sent");
      } else {
        this.db.setStatus(emailId, "needs_expert");
      }
      const st = applyReview(this.db, this.cfg, category, qualifying, note);
      this.db.audit(req.reviewer, `review_${req.action}`, { emailId, modified, qualifying, consecutive: st.consecutive, autonomy: st.autonomy });
      return { status: finalText !== null ? "sent" : "needs_expert", modified, qualifying, categoryState: st, alreadySent };
    });
  }

  /** Post-send quality check of an auto-sent email. */
  qa(emailId: string, ok: boolean, reviewer: string, note: string): ReturnType<InboxDb["getState"]> {
    const email = this.db.getEmail(emailId);
    if (!email || !email.classification) throw new AgentError("email not found", 404);
    if (email.status !== "auto_sent") throw new AgentError("QA applies to auto-sent emails only", 409);
    const d = this.db.latestDraft(emailId);
    this.db.addReview({ emailId, draftId: d?.id ?? 0, category: email.classification.category, action: ok ? "qa_pass" : "qa_fail", reviewer, editedText: null, finalText: null, reason: note, modified: false, qualifying: ok });
    return applyQa(this.db, email.classification.category, ok, reviewer, note);
  }
}

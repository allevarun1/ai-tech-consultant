# AI_LOG

Assistant used: **Claude Code** (this repository was built in one agentic session). The full conversation history must be kept and submitted alongside this repo. This page records only interactions that really happened; it is not padded.

## Your prompts in this session (verbatim intent)
1. Asked for help preparing the "AI Tech Consultant" capstone, pointing at the Notion reference guide. *Useful because:* it made the assistant read the whole guide (the page lazy-loads; the saved HTML only held its tail) before planning.
2. "Go through each step and follow the exact things mentioned in this file and complete the task by opening a new file and do everything there." *Useful because:* it fixed the scope (all 12 guide sections, one new repo, no edits to other projects).
3. "Go ahead and start building it." *Useful because:* it authorised the build after the plan; the assistant then worked task by task with tests and commits.

> **To complete before submitting:** the brief asks for *five* useful prompts of your own. Only the three above exist in the retained session. Add the remaining ones from your own later sessions (for example: asking for a specific bug fix, a design alternative, or a review). **Do not invent prompts.**

## AI-generated work that was rejected, and why
1. **Out-of-scope questions were answered.** The first Task 1 answer logic returned text for "What is the capital of France?" (it matched the word *capital* in an article about capital letters) and for "How do I reset my Netflix password?". Measured on the 26-case set: 65.4% pass. Rejected as unsafe (a support bot must say "I don't know"), replaced with abstention rules (unseen-term ratio, injection pattern, evidence threshold) -> 84.6%, with the remaining failures kept and documented rather than tuned away.
2. **A perfect score that was not evidence.** The rule-based transcript extractor scored 30/30 on cases it had been tuned against. Rejected as proof of quality; six unseen-phrasing cases were added and the **first** run recorded: **1/6**. Vocabulary was then widened (6/6, no longer held out) and the true weakness is stated in `EVALS.md` and `DESIGN.md`.
3. **A test that proved nothing.** One Task 3 test filtered out exactly the rows it meant to inspect (knowledge-outage rows are `needs_clarification` by design). Caught when it failed, fixed to assert the outage is visible and never reported as "existing" or "custom".

(No human reviewer rejection is recorded here - add one if you have it.)

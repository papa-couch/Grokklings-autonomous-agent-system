# Second survey, before phase 5 — August 2026

A targeted follow-up to [the August ecosystem survey](2026-08-ecosystem.md),
done before writing the brain. Three things to settle: what's ready-made for
the brain's four jobs, whether the one ⚠️ claim that survey flagged as
"verify before phase 5" holds up, and whether anything exists for judging
the judge ([open question 8](../open-questions.md)).

**How to read the marks** — same as the first survey.

- ✅ **verified** — I opened the repository card or the official
  documentation myself and read what's written here.
- ⚠️ **unverified** — taken from a survey article or an aggregator, not
  checked against the primary source.

Where a finding says "read off the repository's card," that means the
README, not the code. Nothing here has been run.

---

## 1. The 3-to-7 ceiling doesn't exist — closes open question 2

The first survey carried a ⚠️ claim that the OpenAI Agents SDK names three
to seven agents as a practical ceiling past which picking a receiver starts
to go wrong, with the instruction: **verify this before relying on it in
phase 5.** Doing that now, because the brain's whole job is growing the
roster.

Checked two primary sources:

- **`openai.github.io/openai-agents-python/handoffs/`** ✅ — no limit and no
  ceiling stated anywhere. The only guidance on multiple destinations: "If
  you have multiple possible destinations, register one handoff per
  destination and let the model choose among them."
- **`developers.openai.com/api/docs/guides/agents/orchestration`** ✅ — no
  numeric guidance either. Its examples show two to three agents, but as
  illustration, not prescription.

**The number is unfounded.** Don't build a cap into the brain on its
authority.

What the primary source *does* say is more useful to the brain than a
number would have been, because it's a criterion rather than a threshold:

> "Start with one agent whenever you can. Add specialists only when they
> materially improve capability isolation, policy isolation, prompt clarity,
> or trace legibility."

and

> "Splitting too early creates more prompts, more traces, and more approval
> surfaces without necessarily making the workflow better."

That's directly usable as the bar the brain's "propose a new role" and
"propose splitting a slot" proposals have to clear — and as a bias toward
*not* growing, which a system whose job is proposing growth needs built in.

---

## 2. GEPA — the brain's context editing

`github.com/gepa-ai/gepa` · MIT ✅

The first survey found this and [0010](../decisions/0010-brain-scope.md)
proposed it for the brain's context-editing job. Two corrections and one
addition, all from the repository card and the paper listing:

**The results are no longer only the project's own claim.** ✅ The first
survey noted "the claimed results (35x fewer runs than reinforcement
learning) come from the README and haven't been checked by anyone outside
the project." The paper — *GEPA: Reflective Prompt Evolution Can Outperform
Reinforcement Learning*, arXiv 2507.19457 — is **accepted at ICLR 2026 as
an Oral**. That's outside review at a top venue. The specific figures from
the abstract: outperforms GRPO by 6 percentage points on average and up to
19pp, using up to 35x fewer rollouts; beats MIPROv2 by over 10pp.

⚠️ Peer review is not replication. An Oral means the method and evidence
convinced reviewers, not that anyone re-ran it on our kind of workload.

**It optimizes more than prompts.** ✅ The card says textual parameters
generally — "prompts, code, agent architectures, configurations." That
matters here specifically: our slots *are* configuration, written in words
in a TOML file. The thing GEPA edits and the thing our brain would propose
editing are the same kind of object, which is not true for most frameworks
where an agent is code.

**The adapter interface is two methods.** ✅ `GEPAAdapter` requires
`evaluate` and `make_reflective_dataset`; GEPA does the search. Built-in
adapters: DefaultAdapter, ConfidenceAdapter, DSPy, Generic RAG, MCP,
TerminalBench, AnyMaths, LangChain.

**Its three requirements, against what we now have** — this is the part
that changed since the first survey:

| GEPA needs | We have it? |
|---|---|
| an evaluation metric | ✅ since [0015](../decisions/0015-success-is-the-users-answer.md) — the user's `[success]` criteria |
| a training/validation set (3 examples is enough) | ✅ finished tasks in the journal |
| feedback, not just a score | ✅ `Assessment.feedback`, required by the schema to be actionable |

The prerequisite 0010 named — "before adopting GEPA, we need a
result-quality function" — is met. **This is now adoptable**, where at the
first survey it wasn't.

---

## 3. Judging the judge — open question 8

Built the evaluator in [0015](../decisions/0015-success-is-the-users-answer.md)
and immediately filed the question of whether it judges the way the user
meant. Two things exist for exactly this.

### CJE — Causal Judge Evaluation ✅

`github.com/cimo-labs/cje` · MIT · a library (`pip install cje-eval`)

Read off the repository card: calibrates LLM-judge scores against
ground-truth labels, because "LLM-judge scores are cheap and plentiful, but
their scale can differ materially from the outcome you actually care
about." Inputs: judge scores on everything, plus a randomly sampled slice
of oracle labels — "human ratings, expert review, or a downstream KPI." No
fixed minimum; it adapts to the label budget. (An aggregator described the
slice as 5–25% of samples; ⚠️ that range is not on the card.)

Outputs: calibrated estimates with 95% confidence intervals accounting for
both sampling and label uncertainty, diagnostic badges when judge scores
land outside the labeled range, and transport audits for whether a
calibration generalizes.

**This is the method open question 8 already describes**, done properly and
with uncertainty attached. What that question specifies by hand — score
tasks the user has judged, compare, examine disagreements — CJE does with
statistics that say how much to trust the result.

### llm-judge-calibrator ✅

`github.com/joaquinhuigomez/llm-judge-calibrator` · MIT

Detects three judge biases, via position-swap experiments scored with
Cohen's Kappa:

- **position bias** — favoring whichever response came first;
- **verbosity bias** — rating longer answers higher;
- **self-preference bias** — rating a model's own output higher than
  another model's.

**The third one is aimed straight at us.** Our evaluator judges Grok's
output using Grok — `GrokEvaluator` defaults to the same model family the
workers use. That is the textbook setup for self-preference bias, and
nothing in 0015 accounts for it.

⚠️ How large the effect is, and whether it applies when a model judges
another instance of *itself* rather than a competitor, is not established
by this card — the tool defines and measures the bias, it doesn't quantify
it for our case. Worth checking against the literature before assuming
either that it's fatal or that it's negligible.

The cheap mitigation, if it turns out to matter: let `[success]` name a
different model for judging than the slots use. The config already carries
`model` in the `[success]` section, so this is a documentation change and a
test, not an architecture change.

---

## 4. Self-evolving agent systems — closer to us than the first survey thought

The first survey's closing section claimed three things nobody else has.
Two of them need weakening. Recording that honestly, because the value of
that section is only as high as its accuracy.

### EvoAgentX ✅

`github.com/EvoAgentX/EvoAgentX` · MIT

Read off the repository card: builds multi-agent workflows from a natural
language goal (`WorkFlowGenerator`), then refines them through iterative
feedback with a "self-evolving engine" that improves workflows without
constant human intervention. Four integrated optimizers: TextGrad, MIPRO,
AFlow, EvoPrompt. Supports human-in-the-loop modes that "pause an agent's
execution for manual approval." Requires a goal, an evaluation metric, and
a dataset — the same prerequisites as GEPA.

**This overlaps claims 1 and 3 of "what nobody else has."** Workflows
generated from natural-language descriptions is close to our "a slot is
described in words, in the config, not in code," and a self-evolving engine
with HITL approval is close to "growth by consent."

What still looks distinct, stated carefully rather than defensively:

- ⚠️ EvoAgentX's HITL, on the card, pauses **execution** for approval. Our
  consent is over **changes to the roster** — a different thing to approve.
  Not confirmed either way from the card; would need the docs or the code.
- Its evolution is automatic by default, with approval optional. Ours is
  the reverse by design: a proposal is inert until accepted.
- ⚠️ "Reports back on whether the role paid off, or should be rolled back"
  — no equivalent found on the card. Still looks like ours, but absence
  from a README is weak evidence.

### BerriAI/self-improving-agent ✅

`github.com/BerriAI/self-improving-agent` · MIT

Read off the card: the agent proposes a minimal diff, a human approves, a
draft PR opens. The approval is enforced in several layers — the tool
schema requires `userConfirmedInThisMessage: true` and the executor throws
on false; an `onBeforeApply` hook can reject; the GitHub token is scoped to
one repo.

The consent loop we described as unique exists here, built carefully. The
difference that holds: it edits **existing code files** and explicitly does
not touch the system prompt — "Do not touch the user's system prompt." It
improves an agent; it doesn't propose a new one.

Worth stealing regardless of the rest: **making approval a type-level
requirement rather than a convention.** A proposal that cannot be applied
without a flag that only a human turn can set is a stronger guarantee than
a proposal that politely waits.

---

## What this changes

| | Before | After |
|---|---|---|
| The 3-to-7 ceiling | ⚠️, "verify before phase 5" | **unfounded** — closes open question 2 |
| GEPA's evidence | the project's own README | **ICLR 2026 Oral**, ⚠️ not replicated |
| GEPA's adoptability | blocked, no quality metric | **unblocked** by [0015](../decisions/0015-success-is-the-users-answer.md) |
| Judging the judge | no tooling known | **CJE** ✅, plus bias detection |
| Self-preference bias | not considered | **we judge Grok with Grok** — a named, measurable risk |
| "Nobody else has this" | three claims | **one and a half hold**; see above |

Nothing here has been run, and no decision is made on it. Adopting GEPA,
adopting CJE, and what to do about self-preference are each their own
decision record, and each should say what it costs.

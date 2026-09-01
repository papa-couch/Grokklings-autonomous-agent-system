# 0016. The brain proposes; it never applies

- Status: **accepted**
- Date: 2026-08-27
- Related: [0010](0010-brain-scope.md), [0015](0015-success-is-the-users-answer.md), [the second survey](../research/2026-08-before-phase-5.md)

## Context

Phase 5. The brain reads what happened and suggests the roster should
change: a role that doesn't exist should, a slot is doing two jobs, a
context is producing bad results, a slot isn't earning its place.

Two things unblocked this. [0015](0015-success-is-the-users-answer.md) gave
it a quality signal to read. The
[second survey](../research/2026-08-before-phase-5.md) settled that the
"three to seven slots" ceiling doesn't exist, so the brain needs no
arbitrary cap — and turned up better material than a cap would have been.

The survey also found that this territory is less empty than the first one
claimed: `EvoAgentX` evolves workflows automatically with optional
human-in-the-loop, and `BerriAI/self-improving-agent` runs a
propose-approve-PR loop. Borrowing from both rather than reinventing.

## Decision

**A proposal is a record, not a deferred action.** `Proposal` carries a
kind, a target, a rationale, the evidence it rests on, which bars it
clears, and the concrete change as TOML. Nothing in the colony reads
proposals — the dispatcher, the slots, and the intake don't know they
exist. Recording one changes nothing.

**Consent is a required argument.** `apply(journal, proposal, confirmed)`
raises `NotConfirmed` when `confirmed` is false. Borrowed directly from
`BerriAI/self-improving-agent`, where the same guarantee lives in the tool
schema (`userConfirmedInThisMessage: true`, and the executor throws on
false) rather than in a docstring. A caller that forgets gets an exception,
not a silent change. This is the difference between a system that proposes
and one that proposes with a delay on it.

**The brain never edits the config.** Accepting prints the TOML to put in
it. What the colony runs stays something the user typed — which also means
rolling back is deleting a few lines they can see, not finding what a
program changed.

**Every proposal must be grounded, and the bar is borrowed.** A proposal
citing no evidence, or clearing none of capability isolation / policy
isolation / prompt clarity / trace legibility, is dropped before the user
ever sees it. Those four come from the OpenAI Agents SDK orchestration
guidance, along with its bias against growth: "splitting too early creates
more prompts, more traces, and more approval surfaces without necessarily
making the workflow better." A component whose job is proposing growth
needs that bias built in, not bolted on.

**A payoff verdict needs enough data to be one.** `payoff()` compares a
slot's scores before and after an acceptance and returns "not enough yet"
below three samples on either side, rather than a verdict from two points.
A weak verdict is worse than none here, because this one gets acted on.

**It runs outside the pipeline, on demand** — `grokklings brain` to propose,
`grokklings proposals` to review, accept, or reject. Same rule as scoring: a
task's fate never depends on what the brain is doing.

## Consequences

- The colony cannot improve itself unattended, by construction. That is
  the intent, and it is a real cost: a user who never runs `proposals`
  gets no benefit from the brain at all. `EvoAgentX`'s automatic evolution
  would be faster and is a live alternative if this proves too slow.
- Rejections are recorded, not discarded — the same evidence will come back
  next pass, and a record of "we already said no to this" is what stops the
  brain re-proposing it forever. Nothing reads that history yet; the brain
  is not shown its own rejections. That's a gap, filed below.
- Proposal quality is unmeasured, like the router's picking and the
  evaluator's judging before it. Worse here: a plausible-sounding proposal
  costs the owner's attention and trust, and there's no signal for it
  short of asking them.
- Context editing (0010's GEPA half) is *not* built. `edit_context` is a
  proposal kind the brain can return, written by the model in one pass —
  not an optimizer. Adopting GEPA remains its own decision.

## What's left, named honestly

- The brain doesn't see its own rejection history, so it can re-propose
  what was already turned down.
- No cap on how many proposals one pass can produce.
- `payoff` compares means with no test for whether the difference is real.
  Three samples either side clears "not enough to speak," not "enough to
  be sure."

## How to undo it

Delete `grokklings/brain.py`, the `brain` and `proposals` commands, and
`tests/test_brain.py`. Nothing else imports any of it — the colony has no
idea the brain exists, which is the same property that makes it safe. Any
proposals already in the journal become inert rows, which is what they
already were.

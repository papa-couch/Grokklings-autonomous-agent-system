# 0010. Phase 5's brain: editing context is borrowed, proposing roles is ours

- Status: **accepted** — adopted 2026-08-27, see [0018](0018-gepa-optimizes-context.md)
- Date: 2026-08-27

## Context

The brain in section 9 does four distinct things: proposes a new role,
proposes splitting a slot, proposes reslicing the work, and proposes editing
context. That last one is a separate, well-established problem: optimizing
prompts from execution traces.

## Proposal

Split the brain in two. Hand context editing to a ready-made optimizer
(GEPA, MIT license). Write proposing new roles, splitting a slot, and
getting the user's consent ourselves — nothing ready-made exists for that.

## An important correction

I first said our journal already is the execution traces GEPA needs, so
half the brain is basically built. **That's wrong, and here's what checking
the primary source turned up.**

GEPA needs three things: a quality metric, a training/validation set (as
few as three examples is enough), and an evaluation function that returns
not just a score but actionable feedback — an error message, diagnostics.

The journal alone isn't enough. Before adopting GEPA, we need a
result-quality function for a worker — an answer to "what counts as
success," which the design doc has the user fill in as part of the brain's
context. That's not a small thing; it's a standalone piece of work, and it
comes before everything else in phase 5.

## What it buys

One of the brain's four responsibilities gets taken off our plate — but only
once a quality metric exists.

## What it costs

Quality scoring is the most contentious part of the whole system. Get it
wrong, and the optimizer will diligently make the colony worse, and we
won't notice right away.

## Update, 2026-08-27: the prerequisite is met

The blocker named above — "before adopting GEPA, we need a result-quality
function" — is gone. [0015](0015-success-is-the-users-answer.md) put the
definition of success in the user's hands and made it configuration, and
`Assessment` carries a score, a met/not-met verdict, and actionable
feedback. That's GEPA's three requirements, in the shape it asks for them.

Two things from the [second survey](../research/2026-08-before-phase-5.md)
change the picture around this proposal, neither of them reversing it:

- GEPA's evidence is no longer only its own README — the paper is an ICLR
  2026 Oral. ⚠️ Still not replicated on anything like our workload.
- The "three to seven agents" ceiling this record's context leaned on
  doesn't exist in the primary sources. The brain gets no cap from it —
  but the SDK's qualitative bar ("add a specialist only when it materially
  improves capability isolation, policy isolation, prompt clarity, or trace
  legibility") is a better test for a proposal anyway, and this proposal
  should adopt it.

**Adopted 2026-08-27.** GEPA is in the code behind an optional extra, and
the split this record proposed is what got built: context editing is
GEPA's, proposing roles is ours ([0016](0016-brain-proposes-never-applies.md)).
See [0018](0018-gepa-optimizes-context.md), including a warning this record
didn't anticipate — optimizing against an uncalibrated judge drives the
instructions toward the judge's bias.

## How to undo it

The split, not the parts: drop GEPA and keep writing context edits in one
pass ([0018](0018-gepa-optimizes-context.md) has the concrete steps). The
evaluation function stays useful either way — the "did the role pay off,
or should it be rolled back" report needs it regardless of whether an
optimizer exists.

# 0018. GEPA optimizes a slot's instructions; the result is still a proposal

- Status: **accepted**
- Date: 2026-08-27
- Related: [0010](0010-brain-scope.md) (fulfilled), [0015](0015-success-is-the-users-answer.md), [0016](0016-brain-proposes-never-applies.md), [0017](0017-calibrate-the-judge.md)

## Context

[0010](0010-brain-scope.md) proposed splitting the brain: hand context
editing to GEPA, write role-proposing ourselves. It sat at **proposed** for
one reason — GEPA needs a quality metric, a training set, and feedback, and
none existed.

All three exist now, and [0016](0016-brain-proposes-never-applies.md) made
the gap obvious: the brain can propose rewriting a slot's context, but it
writes the replacement in one pass. That's a guess informed by evidence,
not a tested improvement. GEPA does the other thing — propose, run, score,
read why, propose again, keep what survives.

| GEPA needs | What [0015](0015-success-is-the-users-answer.md) already built |
|---|---|
| a metric | `Assessment.score`, from the user's own criteria |
| a training set | finished, scored tasks in the journal |
| feedback, not just a score | `Assessment.feedback`, required by schema to be actionable |

That third row is why this works at all, and it was not planned: 0015
demanded actionable feedback because a bare score is useless to a person.
It turns out to be exactly what GEPA's teacher model reads.

## Decision

Adopt GEPA (MIT) behind an optional extra. `grokklings/optimize.py` provides
a `SlotAdapter` implementing GEPA's two required methods, and
`grokklings optimize --slot triage --budget 60` runs the search.

**The optimized context comes back as a `Proposal`, not a change.** It goes
through the same consent gate as everything else the brain suggests
([0016](0016-brain-proposes-never-applies.md)) — recorded as an
`edit_context` proposal, accepted with `grokklings proposals --accept`,
which prints the text to put in the config. A better context that installed
itself would still be the colony rewriting its own instructions, and the
fact that the rewrite is measured doesn't change who gets to decide.

**A budget is required, not advisory.** Each metric call runs the worker
once and the judge once — two API calls — and GEPA will happily spend
whatever it's given. `--budget` defaults to 60 and is the only thing
between a search and an unbounded bill.

**Five scored tasks is the floor.** GEPA's own documentation says three
examples can be enough; three of *our* tasks is an afternoon's noise, and
a context fitted to it will look improved and generalize to nothing.

**A crash and an unjudgeable run are told apart.** Both produce no
assessment, and they teach opposite lessons: one says the instructions
produce something broken, the other says nothing about the instructions at
all. Reporting the second as a failure would have the teacher rewrite
instructions that may be fine — the same "couldn't tell is not was bad"
distinction [0015](0015-success-is-the-users-answer.md) drew for scoring.

## Consequences

- **This can be run without ever calibrating the judge**, and shouldn't be.
  GEPA optimizes toward whatever the judge rewards. If the judge is
  miscalibrated ([0017](0017-calibrate-the-judge.md)), the search will
  faithfully drive the instructions toward the judge's bias, and the
  resulting context will score beautifully and serve the user worse.
  Nothing in the code enforces the ordering; this record is the warning.
- Cost is real and per-run: a 60-call budget is 120 model calls.
- GEPA brings its own dependency tree, confined to an extra, so
  [0001](0001-python-stdlib.md) still holds for anyone who doesn't optimize.
- `optimize_context` returns the best candidate GEPA found on the training
  set. There is no held-out validation here, so the reported gain is
  optimistic by construction. Filed below.

## What's left, named honestly

- No held-out set: the score improvement is measured on the tasks the
  search used. A proper split would cost more tasks than most colonies
  will have early on, which is a reason to be careful, not a reason the
  number is honest.
- Nothing warns when optimizing against an uncalibrated judge.
- One slot at a time; GEPA can optimize several components at once and
  this doesn't use that.

## How to undo it

Delete `grokklings/optimize.py`, the `optimize` command, its tests, and the
`optimize` extra. Proposals already recorded stay as they are — inert rows
someone can accept or ignore, same as any other.

## Sources

- `github.com/gepa-ai/gepa` ✅ — MIT. Verified by installing 0.1.4 and
  driving it: a planted colony where the wanted instruction was "cite the
  source" went from 0.20 to 1.00, with the teacher consulted once.
- ⚠️ **A documented-optional method isn't optional in 0.1.4.** The
  `GEPAAdapter` docstring calls `propose_new_texts` optional and says GEPA
  provides a default. The engine reads `self.adapter.propose_new_texts`
  directly rather than through `getattr`, so an adapter that omits it
  raises `AttributeError` mid-search; reflection then silently stops
  proposing and the "optimized" context comes back unchanged with no error
  surfaced to the caller. `SlotAdapter` declares `propose_new_texts = None`
  for this reason. Found by running the engine, not by reading the docs —
  the adapter passed every test written against the documented interface
  while optimizing nothing.

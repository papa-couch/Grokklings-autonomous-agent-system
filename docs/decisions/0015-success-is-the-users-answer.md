# 0015. Success is the user's answer, not the colony's metric

- Status: **accepted**
- Date: 2026-08-27
- Related: [0010](0010-brain-scope.md), [open-questions.md](../open-questions.md) (question 3)

## Context

Phase 5 — the brain — has been blocked from the start, and not on code.
[0010](0010-brain-scope.md) established that every one of the brain's four
jobs needs a way to tell "this got better" from "this got worse," and that
no such function exists. Open question 3 recorded it as "not a technical
task: it needs the user's answer."

The temptation was to invent a metric — task throughput, failure rate,
route length. Every one of those measures the machinery rather than the
work. A colony that processes twice as many tasks twice as badly scores
better on all of them.

The user's answer, when asked, was that there is no single metric to find:
success is individual — different people, different tasks, different
definitions of a good result — and the user should write their own criteria
down, the same as they already write what each slot does.

That's not a limitation to work around. It's the design.

## Decision

Success criteria are **configuration, in the user's own words**, in a
`[success]` section with the three questions from section 9 of the design
doc:

```toml
[success]
looking_for = "what we're after at all"
counts_as   = "what a good result looks like"
never       = "what must never happen, whatever the score"
```

A slot may add its own `success = "..."` for what a good result from that
slot looks like. It's read only when scoring — the worker never sees it, so
a worker can't be graded on a rubric it was shown.

`grokklings/evaluate.py` applies them: `GrokEvaluator` scores a finished
task and returns an `Assessment` — a score from 0 to 1, whether it met the
bar, actionable feedback, and any red lines crossed.

Three properties, each chosen against a plausible alternative:

- **A crossed red line is not a low score.** Work can be excellent at what
  it was asked to do and still do something forbidden. Folding violations
  into the score would average away exactly the case worth catching, so
  they're a separate field, and the CLI exits non-zero on them.
- **"Couldn't tell" is not "was bad."** A failed or unparseable scoring
  call records nothing and returns `None`, rather than a zero. A zero would
  poison every average built on top of it, and the task would look judged
  when it wasn't. Unscored tasks are picked up by the next pass.
- **Scoring runs outside the pipeline, on demand** (`grokklings score`), over
  finished tasks. A task's fate never depends on whether anyone got around
  to judging it — which is what `architecture.md` already required of the
  brain: a separate process on its own cadence, not something reaching into
  the task pipeline.

Assessments are written to the journal as `task_scored` events. That's the
input the brain reads.

**A colony with no `[success]` section is scored not at all**, and is the
same colony it was before this record.

## Consequences

- Open question 3 is closed, and phase 5 is unblocked: 0010's prerequisite
  — "a result-quality function comes before everything else in phase 5" —
  now exists.
- The quality of the criteria is now the user's problem, and it's a real
  one. 0010's warning stands and transfers: criteria written badly will
  have the brain diligently make the colony worse. What's changed is that
  the criteria are visible in the config and can be argued with, rather
  than buried in a metric nobody wrote down.
- Scoring costs a call per task, and none of it is cached or batched.
  Bounded by being on-demand rather than automatic.
- Whether the evaluator *judges* well is unmeasured, exactly as the
  router's picking is. Filed as open question 8.

## How to undo it

Delete the `[success]` section: nothing is scored, no code changes. To
remove it entirely, revert this commit — `grokklings/evaluate.py` and the
`score` command go away, and `SlotSpec.success` with them. Nothing in the
pipeline reads any of it, so nothing else changes.

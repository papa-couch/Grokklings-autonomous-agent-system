# Decision records

One decision, one file. Numbering is sequential; files are never rewritten
after the fact — if a decision gets reversed, its status changes and a link
points at whatever replaced it.

## Statuses

| Status | What it means |
|---|---|
| **accepted** | this is how the code works right now |
| **proposed** | under discussion, not in the code |
| **deferred** | deliberately put off, with a threshold for revisiting it |
| **superseded** | no longer in effect, with a note on what replaced it |

## Format

```
# NNNN. A short statement of the decision

- Status: accepted | proposed | deferred | superseded
- Date:
- Related:

## Context      — what forced a choice
## Decision     — what was chosen
## Consequences — what it costs, including the bad parts
## How to undo it — concrete rollback steps
## Sources      — if the decision leans on someone else's experience
```

The "how to undo it" section is mandatory. A decision whose rollback isn't
described isn't a decision — it's a habit.

## Index

### Accepted

| # | Decision |
|---|---|
| [0001](0001-python-stdlib.md) | The core runs on Python 3.11's standard library |
| [0002](0002-queue-in-database.md) | The queue lives in the database, next to the journal |
| [0003](0003-output-is-a-slot.md) | An output is a terminal slot, not a separate mechanism |
| [0004](0004-attempts-per-step.md) | Attempts are counted per step, not per task |
| [0005](0005-explicit-fail-is-final.md) | An explicit "fail" verdict never retries |
| [0006](0006-instance-reservation.md) | An instance is claimed on assignment, released when its job finishes |
| [0012](0012-step-idempotency.md) | A non-repeatable step inside a worker |
| [0013](0013-grok-primary-backend.md) | Grok is the primary model-backed worker |
| [0014](0014-grok-router.md) | Picking a slot is Grok's job, not a vector index's |
| [0015](0015-success-is-the-users-answer.md) | Success is the user's answer, not the colony's metric |
| [0016](0016-brain-proposes-never-applies.md) | The brain proposes; it never applies |
| [0017](0017-calibrate-the-judge.md) | The judge gets checked against you, with CJE |
| [0010](0010-brain-scope.md) | Phase 5's brain: editing context is borrowed, proposing roles is ours |
| [0018](0018-gepa-optimizes-context.md) | GEPA optimizes a slot's instructions; the result is still a proposal |
| [0019](0019-clones-scale-on-a-hold-time.md) | Clones scale on a hold time, not on the current queue |
| [0007](0007-otel-event-names.md) | The journal speaks OpenTelemetry — by translation, not by renaming |
| [0020](0020-cost-and-limits.md) | An unpriced model is unknown cost, not free |
| [0021](0021-a-dashboard-that-cannot-act.md) | A dashboard that can look and cannot act |
| [0022](0022-mit-license.md) | MIT, so the repository can actually be used |
| [0023](0023-ci-checks-both-promises.md) | CI checks both promises, and fails on silence |

### Proposed and deferred

| # | Proposal | Status |
|---|---|---|
| [0009](0009-memory-not-ours.md) | Phase 4's memory — borrowed, not built | proposed |
| [0011](0011-durable-core.md) | Swapping the core for a durable-execution engine | deferred |

### Superseded

| # | Decision | Replaced by |
|---|---|---|
| [0008](0008-semantic-routing.md) | Picking a slot in phase 2 — with a ready-made library | [0014](0014-grok-router.md) |

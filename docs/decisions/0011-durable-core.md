# 0011. Swapping the core for a durable-execution engine

- Status: **deferred**
- Date: 2026-08-27
- Related: [0001](0001-python-stdlib.md), [0002](0002-queue-in-database.md)

## Context

The queue, the lease, the retries, and the crash recovery we hand-wrote are
a well-established category called durable execution. Verified against the
primary sources:

- **Hatchet** (MIT, Postgres, SDKs for Python, TS, Go, Ruby) — down to
  matching terminology: they call it "worker slots" too. Beyond what we
  have: concurrency keys on a dynamic value, rate limits, durable sleep,
  routing by worker labels, OTel and Prometheus, a web UI.
- **DBOS Transact** (MIT, a library, not a service) — closer to our
  philosophy: `@DBOS.workflow()` and `@DBOS.step()` decorators, checkpoints,
  recovery from the last step, queues with concurrency and rate limits,
  cron, idempotency via `SetWorkflowID`.

## Why deferred, not rejected

Both options need Postgres and bury [0001](0001-python-stdlib.md). As long
as the colony fits on one machine, that price doesn't pay for itself.

There's an open question about DBOS: SQLite is listed in the repository's
tags, but the README only spells out Postgres. If SQLite is genuinely
supported, swapping the core is possible **without** losing how simple it
is to run, and then this decision needs revisiting right away. See
[open-questions.md](../open-questions.md).

## Threshold for revisiting this

Any one of these three:

1. Tasks stop fitting on one machine.
2. Concurrency keyed on a dynamic value ("no more than two tasks for one
   contract address") becomes necessary — building that ourselves is real work.
3. It's confirmed that DBOS runs on SQLite.

## What to take right now, without adopting anything

This doesn't require swapping the core:

- the retry / replay / fork distinction — we only have retry, and it's
  recorded as work in [open-questions.md](../open-questions.md);
- ~~step-level idempotency, not just at the colony's entry point~~ — **done**,
  see [0012](0012-step-idempotency.md).

## How to undo it

This decision is deferred, not made — there's nothing to undo. If we do
adopt one of these engines, the "slot, verdict, brain" layers carry over,
and `journal.py` and the dispatcher's loop go away entirely.

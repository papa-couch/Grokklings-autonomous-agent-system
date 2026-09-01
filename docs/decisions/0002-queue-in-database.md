# 0002. The queue lives in the database, next to the journal

- Status: accepted
- Date: 2026-08-27
- Related: [0001](0001-python-stdlib.md)

## Context

The design doc requires: "a task never disappears, every outcome is
recorded in the journal." A queue in process memory can't hold that up — a
restart eats anything that hadn't finished yet.

## Decision

The queue is the `tasks` table, in the same database as the event journal.
A task in progress holds a lease (`lease_until`); once the lease expires,
the task goes back to the queue and is picked up by another instance
(`Journal.recover_expired`).

## Consequences

A restart loses no tasks. A crashed instance doesn't take its task down with
it. Submission works on a stopped colony: `submit` writes to the same
database.

The cost: every step of a task is a write to disk, and throughput is capped
by SQLite. Writing from several processes is possible but not seriously
built for.

There used to be a known hole here: idempotency only worked at the colony's
entry point, so a worker that reached the outside world and crashed before
returning a verdict would go out there again on retry. That's now closed by
[0012](0012-step-idempotency.md) — see
[open-questions.md](../open-questions.md) for what's still not closed there.

## How to undo it

Swap `Journal` for an external queue. There's one seam: the dispatcher only
touches storage through `Journal`'s methods (`peek`, `claim`, `requeue`,
`finish`, `recover_expired`).

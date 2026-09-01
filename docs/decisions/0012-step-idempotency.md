# 0012. A non-repeatable step inside a worker

- Status: accepted
- Date: 2026-08-27
- Related: [0002](0002-queue-in-database.md), [0003](0003-output-is-a-slot.md)

## Context

The deduplication key only worked at the colony's entry point. There was no
protection inside a task's lifetime: a worker that reached out to the
outside world and crashed before returning a verdict would go out there
again on retry.

This wasn't a hypothesis — it was our own bug. The file output would append
a second identical line. The webhook output would send a second
notification. Both reproduce by crashing right after a successful delivery.

## Decision

Workers got a primitive, `ctx.once(key, fn)` — "do this at most once per
task's lifetime":

```python
code = ctx.once("deliver", deliver)
```

A completed step's result is written to the `steps` table, keyed by (task,
slot, step key). On a retry, `once` returns the recorded value instead of
calling `fn` again. The key is the worker's choice, and it must stay the
same across attempts; the attempt number and the instance are deliberately
left out of it.

A failed step isn't recorded — so it honestly retries. Otherwise the
mechanism would turn into a way of losing work.

For a coroutine worker, there's `ctx.once_async(key, fn)`.

## An honest boundary

There are two gaps here, and they close in different ways. This is the main
thing to understand about this record.

| Gap | What happens | Closed? |
|---|---|---|
| **A** | the step succeeds -> we crash -> a retry redoes it | **yes**, by `once()` |
| **B** | an external service accepts the request -> we crash before hearing back | **no** — there's no way to find out |

Gap B can't be closed alone by anything — not by us, not by Temporal, not by
DBOS. It needs cooperation from the receiving side. That's the other half of
the decision: `ctx.idempotency_key(name)` — a stable key, the same across
every attempt of one step and different across tasks and slots. A service
that honors it drops the repeat on its own.

The webhook output now sends it as the `Idempotency-Key` header (the header
name is configurable via the `idempotency_header` option).

## Consequences

A step's result must serialize to JSON — it has to survive a restart.
Otherwise `once` raises a clear error instead of silently corrupting the
record.

Step records live exactly as long as a task can still be retried:
`Journal.finish` deletes them on any terminal status. The table doesn't grow
without bound, but that also means there's nothing to look at once a task is
done — for history, there's the journal, which gets `step_completed` and
`step_replayed` events.

The mechanism is optional. A worker that never calls `once` behaves exactly
as it did before. That's deliberate: forcing every action to be wrapped
would cost more than it's worth, and not every action is harmed by running
twice.

## How to undo it

Remove the `once` calls from the outputs and the method itself from
`WorkerContext`; the `steps` table would simply stop filling up.
`tests/test_idempotency.py` shows exactly what would be lost.

Verified by the opposite test too: with the mechanism disabled, five of the
twelve tests fail, including both output tests.

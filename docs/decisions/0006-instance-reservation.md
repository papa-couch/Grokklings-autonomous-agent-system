# 0006. An instance is claimed on assignment, released when its job finishes

- Status: accepted
- Date: 2026-08-27

## Context

Two bugs found by tests, both about the exact moment an instance counts as busy.

The "busy" flag was set inside the worker's coroutine. A coroutine doesn't
start immediately — only once the event loop gets around to it — so a
single dispatcher pass could hand one instance several tasks.

Release lived in the same place, in a `finally`. If a job got cancelled
before its coroutine ever started, that `finally` never ran, and the
instance stayed busy forever — the slot quietly lost capacity.

## Decision

`SlotRuntime.reserve()` marks an instance busy synchronously, right at the
moment it's handed out. `Dispatcher._job_done` releases it — a callback on
job completion that fires on every outcome, including cancellation before
the job ever starts. The rule: whoever claims it also releases it.

## Consequences

Slot capacity is now exact. The invariant isn't obvious and is easy to
break with an unrelated edit, so it's pinned down by two tests:
`test_instances_are_released_after_every_outcome` and
`test_cancelled_job_does_not_eat_the_instance`.

## How to undo it

Don't. If this code needs touching, both tests must stay green.

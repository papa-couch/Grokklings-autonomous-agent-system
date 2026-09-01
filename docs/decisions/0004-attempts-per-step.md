# 0004. Attempts are counted per step, not per task

- Status: accepted
- Date: 2026-08-27

## Context

The design doc gives a task a single "attempt counter." Read literally, that
has an unpleasant effect: a task that passed through five slots arrives at
the sixth with its budget already spent, and fails for no real reason.

## Decision

`Task.attempts` resets on every hop into a new slot (`Dispatcher._transition`).
The `max_attempts` limit belongs to the slot, not the task. The hop counter,
`Task.hops`, is the opposite — it runs across the whole task and never
resets; that's what guards against a loop.

## Consequences

Worst case for model calls: (`max_hops` + 1) × `max_attempts` — ten hops
means eleven slots on the path, counting the entry slot. At the defaults (10
and 3), that's 33 calls per task. There's no cost limit yet, so this is the
ceiling to keep in mind.

## How to undo it

Remove `task.attempts = 0` from `_transition`. The
`test_crash_is_retried_until_it_succeeds` test will still pass, but the
behavior of long routes changes — add a test with a route longer than the
attempt limit.

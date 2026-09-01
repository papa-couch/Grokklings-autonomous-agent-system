# 0005. An explicit "fail" verdict never retries

- Status: accepted
- Date: 2026-08-27

## Context

In the design doc's failure table, "fail" sits next to a timeout and an
instance crash. But they're different things: a worker that returns "fail"
already thought it through and decided; a worker that crashed decided
nothing.

## Decision

What retries: an instance crash, a timeout, garbage instead of a verdict (a
validation error). What doesn't retry: a `fail` verdict with a reason — it
immediately moves the task to `failed` status and raises an alert.

## Consequences

A worker that returns "fail" for a transient reason (an external service is
down) doesn't get a second chance. The right response to that case isn't
"fail" — it's an exception, which does trigger a retry. That needs to be
written into the slot's context.

## How to undo it

In `Dispatcher._apply`, route the `Decision.FAIL` branch to `_retry_or_fail`
instead of an immediate `finish`. `test_fail_is_terminal_and_journalled`
checks exactly today's behavior and will fail — that's expected.

# 0001. The core runs on Python 3.11's standard library

- Status: accepted
- Date: 2026-08-27
- Related: [0002](0002-queue-in-database.md), [0011](0011-durable-core.md)

## Context

The design doc left the stack and the storage up to the implementation. The
colony needed to work end to end before any money or external infrastructure
went into it.

## Decision

The core uses only the standard library: `sqlite3` for the journal and
queue, `asyncio` for running instances concurrently, `tomllib` for the
config, `http.server` for the incoming webhook. The one optional dependency
is the `anthropic` package, needed only by slots that think with a model
(`pip install 'grokklings[llm]'`).

> Since [0013](0013-grok-primary-backend.md): there are two optional
> dependencies, not one — `grokklings[grok]` (the `openai` package, for the
> primary Grok backend) and `grokklings[anthropic]`. The `llm` extra named
> above no longer exists. The decision itself — a stdlib-only core, models
> behind an optional extra — is unchanged.

## Consequences

Good: `git clone` and `python -m grokklings run` — that's it. No queue, no
database, no broker to stand up. Tests run anywhere.

Bad: everything a ready-made engine already solved, we write ourselves and
with our own bugs. One machine is the ceiling; there's no horizontal scaling.

A synchronous worker doesn't get interrupted by its timeout: `asyncio.wait_for`
cuts off a coroutine, but a regular function keeps running to completion in
its own thread.

## How to undo it

Switch to a durable-execution engine — see
[0011](0011-durable-core.md). The "slot — verdict — brain" layers survive
that move: they don't depend on who's holding the queue.

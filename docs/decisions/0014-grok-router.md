# 0014. Picking a slot is Grok's job, not a vector index's

- Status: **accepted**
- Date: 2026-08-27
- Related: [0008](0008-semantic-routing.md) (superseded), [0001](0001-python-stdlib.md), [0013](0013-grok-primary-backend.md)

## Context

Phase 2's remaining piece: the dispatcher works out the right slot when a
worker hasn't named a receiver. [0008](0008-semantic-routing.md) proposed
the `semantic-router` library — routes described by example phrases,
matched by a local HuggingFace encoder.

That proposal was written before [0013](0013-grok-primary-backend.md). Its
reasoning was "comparing vectors is an order of magnitude cheaper than
calling a model" — true in the abstract, but it assumed a colony where
calling a model is the exception. After 0013 it's the rule: a Grokklings
colony is Grok-backed, and everyone running one already has a key and slots
that call the model on every task. Paying a local encoder's cost — a model
downloaded to disk, the first dependency that breaks "clone and run" — to
avoid one more call in a system already making them per task is the wrong
trade.

## Decision

Slot-picking is a **router**: `async pick(task, candidates) -> str | None`,
configured in a `[routing]` section, defaulting to `GrokRouter`. It's asked
only when the answer isn't already determined, in two places:

1. **Several entry slots.** Previously a config error ("several slots
   marked as entry"). Now legal *when a router is configured* — the router
   picks among them by what each says it does. A colony with one entry slot
   never reaches the router and never pays for a call.
2. **A `not_mine` verdict.** Previously a dead end: straight to the
   unplaced pile. Now the router is offered the slots the task hasn't
   visited, excluding the one that just refused and excluding outputs. A
   refusal becomes a handoff; only a task nobody will take lands on the pile.

The router names one of the candidates or `null`. A name outside the list
is refused, not obeyed — twice, in the router and again in the dispatcher.
The hop counter and the revisit guard apply to a routed handoff exactly as
they do to an explicit one.

**A colony with no `[routing]` section behaves exactly as it did before
this decision.** That's the compromise 0008 sketched and this record takes:
picking is an optional extra, not a core dependency, so
[0001](0001-python-stdlib.md) holds — no key, no network, still a working
colony.

## Consequences

- Routing costs an API call and its latency, where a vector index would
  have been near-instant. Mitigated by only asking when there's a genuine
  choice, but a colony with several entry slots and heavy traffic pays per
  task. Nothing here caches or batches those calls yet.
- Picking quality is unmeasured. 0008 asked for a hit-rate measurement
  against a set of tasks with a known correct slot, with the bar set before
  measuring — that's still the right test and still hasn't been run. Filed
  in [open-questions.md](../open-questions.md).
- A colony without a key loses slot-picking entirely. Deliberate: the
  alternative was a lexical fallback whose misses would be silent and hard
  to tell apart from the model's.
- The dispatcher's `route()` and `_apply()` became coroutines. Contained —
  they were already called from async code.

## How to undo it

Delete the `[routing]` section from the config: the colony reverts to
entry-slot-only routing with no code change. To remove it entirely, revert
this commit — `grokklings/routing.py` goes away, `route()` and `_apply()`
turn back into regular functions, and the config error on several entry
slots comes back.

## Sources

- xAI, "Structured Outputs," docs.x.ai — the router uses the same
  `json_schema` + `strict` response format as the Grok worker, verified in
  [0013](0013-grok-primary-backend.md).

# What's built and where to keep going

> A map of the other documents: [docs/README.md](README.md). The reasoning
> behind decisions already made, and how to undo them: [decisions/](decisions/).

Phase 1 of the design doc is implemented, plus the pieces that were cheaper
to lay in now than to bolt on later. Below is an honest line between what
works and what's left for the phases after this one.

## Core

```
sources -> intake -> queue -> dispatcher -> slots -> output
                        ^                     |
                        └──── verdict ────────┘
```

The queue doesn't live in process memory — it lives in SQLite next to the
journal. That gives the main property: a restart loses no tasks, and a
crashed instance hands its task back once the lease expires
(`Journal.recover_expired`).

The dispatcher is the only one who decides on the route. A worker returns a
verdict and knows nothing about its neighbors, so a separate router
character isn't needed.

## What works

| Design doc section | State |
|---|---|
| 2. Input: manual submission, interval, webhook | done |
| 2. Input: stream subscription (websocket) | not done — needs an external dependency |
| 2. Intake: id, journal, deduplication | done |
| 3. The task itself, result accumulation | done |
| 4. Dispatcher, routing by verdict, loop and revisit guards | done |
| 4. Picking a slot when no receiver was named | done — [0014](decisions/0014-grok-router.md), quality unmeasured |
| 5. The slot and the return contract | done |
| 6. Spawning and collapsing clones | done — [0019](decisions/0019-clones-scale-on-a-hold-time.md); `max_instances` is a ceiling once `[scaling]` is on |
| 7. Failure handling (every row in the table) | done |
| — step non-repeatability across a retry | done, with a caveat — [0012](decisions/0012-step-idempotency.md) |
| 8. The colony's shared memory | not done |
| 9. What counts as success | done — the user's own criteria, [0015](decisions/0015-success-is-the-users-answer.md) |
| 9. The brain: proposes roles, splits, retirements, context edits | done — proposals only, [0016](decisions/0016-brain-proposes-never-applies.md) |
| 9. The brain: optimizing context properly | done — GEPA, [0018](decisions/0018-gepa-optimizes-context.md) |
| 9. "Did the role pay off" report | done — `payoff()`, refuses a verdict on thin data |
| 10. Output, several outputs with a filter | done |
| 11. Observability: live state, journal | done as a CLI |
| 11. A dashboard | done — read-only by construction, [0021](decisions/0021-a-dashboard-that-cannot-act.md) |
| 11. A journal a dashboard can read | done — translated, not renamed, [0007](decisions/0007-otel-event-names.md) |
| 11. Accumulated cost | done — [0020](decisions/0020-cost-and-limits.md); unpriced models are named, not counted as free |
| 11. A cost limit with a stop | done — pauses the colony, never kills it |
| 12. Pause, stop, seeing in-flight tasks through | done |
| 12. Snapshotting and rolling back the roster, editing a slot on the fly | not done |

## Where to build the next phases in

**Phase 2 — done.** See "Picking a slot" above: routing on a named receiver,
plus the router for when nobody named one
([0014](decisions/0014-grok-router.md)). What's left isn't code — it's
measuring whether the router picks *well* (open question 7).

**Phase 3 — done.** `grokklings/scaling.py` is the watcher that was missing:
it runs at the top of each dispatcher tick, reads `Journal.queue_depth()`,
and calls `add_instance` / `remove_instance` one step at a time. Every
decision is a hold time rather than a threshold on the current depth —
otherwise a burst starts it churning — and growing (10s) waits far less
than shrinking (60s), because the two mistakes cost differently. Unrouted
work is attributed to a single entry slot, without which the slot actually
taking the load would never grow
([0019](decisions/0019-clones-scale-on-a-hold-time.md)).

**Phase 4 — shared memory.** The key is `Task.dedup_key`, which is also the
shared-memory key per the design doc. Write through `WorkerContext` (it
already has the journal); store versions with a timestamp and author — like
`Contribution`, not an overwrite. Instance-local memory is already there
(`Instance.memory`); whatever's useful in it should fold into shared memory
on collapse.

**Phase 5 — done.** `grokklings/brain.py` reads the
unplaced pile, the failures, and the `task_scored` events, and returns
proposals: a new role, a split, a context edit, a retirement. Nothing in
the colony reads a proposal — the dispatcher, the slots, and the intake
don't know they exist, which is what keeps a proposing system from being a
self-modifying one. Consent is an argument (`apply(..., confirmed=True)`,
raising otherwise), not a convention, and accepting prints TOML rather than
editing the config
([0016](decisions/0016-brain-proposes-never-applies.md)).

Context editing is GEPA's, behind the `optimize` extra
([0018](decisions/0018-gepa-optimizes-context.md)): `SlotAdapter` runs a
candidate context over scored tasks, judges each with the user's criteria,
and hands GEPA the feedback its teacher model reads. What wins comes back
as an `edit_context` proposal and goes through the same consent gate as
everything else.

Left to watch rather than build: the optimizer aims at whatever the judge
rewards, so an uncalibrated judge ([0017](decisions/0017-calibrate-the-judge.md))
turns a search into a way of encoding its bias. And the brain doesn't read
its own rejection history, so it can re-propose what was turned down.

**Phase 6 — observability and limits.** `Colony.status()` already hands back
a ready-made snapshot of live state — a dashboard just has to display it.
User alerts currently go through `Colony._alert` into the journal and the
log; that's where a real channel gets plugged in.

`grokklings/otel.py` translates the five events that have an OpenTelemetry
equivalent, so a dashboard is an importer rather than an integration
([0007](decisions/0007-otel-event-names.md)). The journal keeps its own
names; nothing was renamed and nothing on disk changed.

`grokklings/cost.py` totals the journal's model calls against a pinned copy
of litellm's price table, and a `[cost]` section turns that total into a
stop — `dispatcher.paused`, the same stop control section 12 already asks
for, so in-flight work is seen through
([0020](decisions/0020-cost-and-limits.md)).

The sharp edge, stated rather than buried: **a model with no price is
counted as unknown, not as free**, and a limit is not enforced against a
total known to be incomplete. That means a runaway unpriced model isn't
stopped — loudly, on every check, rather than silently.

`grokklings/dashboard.py` serves one read-only page of all of it
([0021](decisions/0021-a-dashboard-that-cannot-act.md)). It reads the
database from outside like the brain does, binds to loopback because the
journal holds task payloads, and **implements `do_GET` and no other
method** — so a control panel would have to be added on purpose rather
than arrived at. Buttons are exactly what would turn 0016's consent
guarantee and 0020's stop into questions about who can reach a port.

What's left in this phase: no snapshotting or rolling back the roster, no
editing a slot on the fly, and nothing resumes a paused colony when a daily
window rolls past.

## Picking a slot

`Dispatcher.route()` settles the destination in order: the slot a previous
verdict named, then a single entry slot, and only when neither answers —
the router (`grokklings/routing.py`), picking among the entry slots by their
descriptions. A `not_mine` verdict goes through the same router before the
unplaced pile, offered the slots the task hasn't visited.

The router is user-supplied code behind an `async pick(task, candidates)`
seam, so the dispatcher guards it on both sides: a name outside the
candidate list is refused, and an exception from it falls back to
no-router behavior rather than leaving the task in progress to wait out its
lease. Configured by a `[routing]` section; without one, none of this is
reachable and the colony behaves as it did in phase 1
([0014](decisions/0014-grok-router.md)).

## Decisions we had to make

The design doc left the stack and the storage up to the implementation.

- **Python 3.11, standard library only in the core.** SQLite for the journal
  and queue, asyncio for running instances concurrently, tomllib for the
  config. The `openai` and `anthropic` packages are only needed by slots
  that think with a model (Grok and Claude respectively).
- **An output is a terminal slot, not a separate mechanism.** The doc says
  "a regular worker, just a final one"; that gives an output retries, a
  timeout, and journal entries for free. Several outputs each get the task
  in turn, each with its own attempt count.
- **The attempt counter is per step, not per task.** Otherwise a task that
  passed through five slots would run out of budget for no reason.
- **A synchronous worker doesn't get interrupted by its timeout.** An async
  one is cut off by `asyncio.wait_for`; a regular function keeps running to
  completion in its own thread — there's nothing to interrupt it with. The
  task honestly goes to retry, but the work can end up duplicated, so
  synchronous slots should keep a generous timeout, or the worker should be
  written as a coroutine.
- **An explicit `fail` from a worker never retries.** That's a deliberate
  decision, not a crash; crashes, timeouts, and garbage responses are what
  retry.

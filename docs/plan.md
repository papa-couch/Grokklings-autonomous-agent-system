# Phase plan

Statuses are honest: "done" means covered by tests and verified by running
it, not "written."

| Phase | What the design doc calls for | Status |
|---|---|---|
| 1 | Intake -> dispatcher -> slot -> output, the task, the journal, deduplication | **done** |
| 2 | Several slots, handoffs, loop guard, failure handling | **done** |
| 3 | Spawning and collapsing clones, queue metrics | **done** — [0019](decisions/0019-clones-scale-on-a-hold-time.md) |
| 4 | The colony's shared memory | not started — candidates checked, [survey](research/2026-08-before-phases-4-and-6.md); all need a database |
| 5 | The brain: journal analytics, the unplaced pile, new roles | **done** — proposes ([0016](decisions/0016-brain-proposes-never-applies.md)), optimizes context with GEPA ([0018](decisions/0018-gepa-optimizes-context.md)) |
| 6 | Live state, a dashboard, cost limits | **done** — cost and limits ([0020](decisions/0020-cost-and-limits.md)), a read-only dashboard ([0021](decisions/0021-a-dashboard-that-cannot-act.md)) |

## What phase 1 actually covers

Verified by 48 tests, including an end-to-end run: webhook -> intake -> slot -> file.

Intake with deduplication that survives a restart. A task with a route, an
accumulated result, and two counters. A dispatcher with a four-verdict return
contract. Retries with growing backoff, timeouts, returning a task after an
instance crashes. Three sources (manual submission, interval, webhook),
three outputs (file, stdout, webhook). A seven-command CLI.

## What phase 2 covers

Routing on a "next" verdict, handoff permissions (`can_route_to`), the
hop-counter loop guard, the revisit guard, the whole failure-handling table
from section 7 — and, since [0014](decisions/0014-grok-router.md), picking
a slot when nobody named one: several entry slots chosen between by
content, and a `not_mine` verdict rehomed instead of dead-ending on the
unplaced pile.

One caveat on "done," in the honest sense this table promises: picking is
covered by tests on a fake client and verified by disabling it, but its
*quality* has never been measured against real traffic — see the hit-rate
question in [open-questions.md](open-questions.md). A colony with no
`[routing]` section behaves exactly as it did in phase 1.

## What phase 5 covers

The brain reads the unplaced pile, the failures, and the scores, and
proposes roster changes — a new role, splitting a slot, editing a context,
retiring a slot. Every proposal cites its evidence and clears a bar or it's
dropped. Accepting is explicit and prints TOML; the brain never edits the
config ([0016](decisions/0016-brain-proposes-never-applies.md)). A payoff
report compares a slot's scores before and after a change and refuses a
verdict on too little data. Context editing is GEPA's, as
[0010](decisions/0010-brain-scope.md) proposed and
[0018](decisions/0018-gepa-optimizes-context.md) built: `grokklings
optimize` searches for better instructions and records the winner as one
more proposal.

The caveats on "done," in the honest sense this table promises:

- **Everything here rests on the judge's numbers**, and the judge is
  uncalibrated until someone labels a sample ([open question
  8](open-questions.md)). An optimizer aimed at a biased judge will
  faithfully drive the instructions toward that bias.
- The optimizer's reported gain is measured on the tasks it searched with —
  no held-out set, so it's optimistic by construction.
- The brain doesn't read its own rejection history and can re-propose what
  was turned down.

## What phase 3 covers

A `[scaling]` section turns `max_instances` into a ceiling and starts a
slot at `min_instances`, with a watcher that grows it under sustained
pressure and gives instances back after sustained idleness. Both waits are
hold times, not thresholds on the current depth, and they're asymmetric on
purpose. Unrouted work counts toward a single entry slot, without which the
slot doing the work would never grow.

Not covered: cost. It scales on queue depth, which tracks work rather than
money, and nothing caps the total across slots — that's phase 6.

## What phase 6 covers

Working: `Colony.status()` returns live state, the journal holds the full
history, the CLI shows both.

Since [0007](decisions/0007-otel-event-names.md), a journal can be read out
in OpenTelemetry's shape, so a dashboard is an importer rather than an
integration — and adopting none costs nothing.

Cost and limits are done ([0020](decisions/0020-cost-and-limits.md)):
`grokklings cost` totals the journal against a pinned price table, and a
`[cost]` section pauses the colony when a limit is crossed. **A model with
no price is unknown cost, not zero** — so a limit isn't enforced against an
incomplete total, and a runaway unpriced model isn't stopped. Said loudly,
not hidden.

`grokklings dashboard` shows all of it on one page that refreshes itself
and **cannot write** — the handler has no method but `do_GET`
([0021](decisions/0021-a-dashboard-that-cannot-act.md)).

The caveats on "done": the page has no authentication, so loopback is the
whole of its access control. Nothing snapshots or rolls back the roster,
nothing edits a slot on the fly, and nothing resumes a paused colony once a
daily window rolls past — that last one still needs a restart.

## What's next, cheapest first

The order is proposed, not settled. The user decides.

1. ~~Step-level idempotency.~~ **Done 2026-08-27** —
   [0012](decisions/0012-step-idempotency.md). This also fixed a bug in our
   own outputs: they were duplicating delivery on a retry.
2. ~~Journal event names on the OpenTelemetry convention.~~ **Done
   2026-08-27** — [0007](decisions/0007-otel-event-names.md), and the
   rename didn't happen: `grokklings/otel.py` translates instead, so stored
   journals are untouched and a Development-status convention costs one
   file when it moves. Five of 33 events have an honest equivalent; the
   other 28 are listed by name as ours.
3. ~~The rest of phase 2.~~ **Done 2026-08-27** —
   [0014](decisions/0014-grok-router.md). The fork between staying simple to
   run and pulling in a ready-made library got resolved a third way: the
   router asks Grok, and a colony without a key keeps working without it.
   What's left is measuring whether it picks *well* — see
   [open-questions.md](open-questions.md).
4. ~~A result-quality function — "what counts as success."~~ **Done
   2026-08-27** — [0015](decisions/0015-success-is-the-users-answer.md). The
   answer turned out to be that there isn't one answer: success is
   individual, so the user writes their criteria in a `[success]` section
   and `grokklings score` applies them. Phase 5's prerequisite from
   [0010](decisions/0010-brain-scope.md) is met. What's unmeasured is
   whether the evaluator judges the way the user meant — open question 8.
5. ~~Phase 3, clones.~~ **Done 2026-08-27** —
   [0019](decisions/0019-clones-scale-on-a-hold-time.md). A `[scaling]`
   section turns on a watcher that spawns and collapses instances on a hold
   time, so a burst doesn't start it churning.
6. ~~Adopt GEPA for context editing.~~ **Done 2026-08-27** —
   [0018](decisions/0018-gepa-optimizes-context.md). `grokklings optimize`
   searches for better instructions and records the winner as a proposal.
7. ~~Calibrate the judge.~~ **Tooling done 2026-08-27** —
   [0017](decisions/0017-calibrate-the-judge.md), `grokklings calibrate`.
   The measurement itself still needs someone to label thirty real tasks;
   [open question 8](open-questions.md). Worth doing **before** trusting
   either the brain's proposals or an optimized context: both are built on
   the judge's numbers.
8. ~~Cost and limits.~~ **Done 2026-08-27** —
   [0020](decisions/0020-cost-and-limits.md). The borrowed part was data,
   not code: a maintained price table, pinned locally, so this cost no new
   dependency.

9. ~~A dashboard.~~ **Done 2026-08-27** —
   [0021](decisions/0021-a-dashboard-that-cannot-act.md). Read-only by
   construction. A control panel is a separate decision, not an increment
   of this one.

10. ~~Cover the three modules nothing was testing directly, and check our
    call shapes against the real SDKs.~~ **Done 2026-08-28** —
    `journal.py`, `slots.py` and `colony.py` carry the colony's durability
    and had no test files of their own; they now have 113 tests between
    them. Separately, `tests/test_sdk_contract.py` binds the requests our
    production code sends against the installed SDKs' real signatures,
    which is the only part of "it has never touched a real API"
    ([question 4](open-questions.md)) that can be closed without a key. It
    found nothing broken today and would have caught the GEPA class of bug.


## What's deliberately left out of this plan

- **A websocket source.** Needs an external dependency, and there's no
  stream to point it at yet.
- **A Telegram output.** Already covered by the existing webhook output.
- **Horizontal scaling.** Runs into [0001](decisions/0001-python-stdlib.md);
  the threshold for reconsidering it is recorded in
  [0011](decisions/0011-durable-core.md).

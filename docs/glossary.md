# Glossary: architecture terms -> names in code

The original design doc that this project builds on was written in Russian;
the identifiers in the code are English. This table bridges the two, so the
source model isn't lost when reading the code.

| Term | In code | Where |
|---|---|---|
| Source | `Source`, `ManualSource`, `IntervalSource`, `WebhookSource` | `grokklings/sources/` |
| Intake | `Intake` | `grokklings/intake.py` |
| Dispatcher | `Dispatcher` | `grokklings/dispatcher.py` |
| Task | `Task` | `grokklings/model.py` |
| Deduplication key | `Task.dedup_key`, `extract_key` | `grokklings/intake.py` |
| Route | `Task.route` | `grokklings/model.py` |
| Accumulated result | `Task.result` (a list of `Contribution`) | `grokklings/model.py` |
| Attempt counter | `Task.attempts` | counted per step, not per task |
| Hop counter | `Task.hops` | limit: `colony.max_hops` |
| Slot | `SlotSpec` (the definition) + `SlotRuntime` (the runtime) | `grokklings/slots.py` |
| Slot instance | `Instance` | `grokklings/slots.py` |
| Spawning / collapsing a clone | `add_instance`, `remove_instance` | driven by `grokklings/scaling.py` |
| Scaling policy | `Policy` <- the `[scaling]` section | hold times, not thresholds |
| Slot context | `SlotSpec.context` -> the worker's system prompt | `grokklings/handlers/_contract.py` |
| Return contract | `Verdict` | `grokklings/model.py` |
| — next: "slot" | `Verdict.next(slot, result)` | `Decision.NEXT` |
| — done | `Verdict.done(result)` | `Decision.DONE` |
| — not mine | `Verdict.not_mine(reason)` | `Decision.NOT_MINE` |
| — fail | `Verdict.fail(reason)` | `Decision.FAIL` |
| Router (picking a slot) | `Router`, `GrokRouter` | `grokklings/routing.py` |
| Success criteria | `Criteria` <- the `[success]` section | `grokklings/evaluate.py` |
| Result quality | `Assessment` (score, met, feedback, violations) | `python -m grokklings score` |
| Judge calibration | `Calibration`, drift, `refused` | `grokklings/calibrate.py`, CJE |
| The brain | `GrokBrain`, `think()` | `grokklings/brain.py` |
| Context optimization | `SlotAdapter`, `optimize_context()` | `grokklings/optimize.py`, GEPA |
| A proposal | `Proposal` -> a `proposal_made` event | `python -m grokklings brain` |
| Growth by consent | `apply(..., confirmed=True)`, `NotConfirmed` | `python -m grokklings proposals` |
| Did the role pay off | `payoff()` | scores before vs. after an acceptance |
| The unplaced pile | tasks with status `TaskStatus.UNPLACED` | `python -m grokklings pile` |
| Journal | `Journal` | `grokklings/journal.py` |
| Journal in OTel's vocabulary | `to_span`, `MAPPING`, `OURS_ALONE` | `grokklings/otel.py` |
| Non-repeatable step | `WorkerContext.once`, `once_async` | the `steps` table |
| Idempotency key | `WorkerContext.idempotency_key` | passed to an external service |
| Output point | a terminal slot (`SlotSpec.terminal`) | `grokklings/outputs/` |
| Accumulated cost | `Spend`, `Prices` <- the pinned table | `python -m grokklings cost` |
| Cost limit | `Budget`, `Guard` <- the `[cost]` section | pauses; never kills |
| Live state | `Colony.status()` | `python -m grokklings status` |
| The dashboard | `snapshot()`, `do_GET` only | `python -m grokklings dashboard` |
| Stop control | `Dispatcher.paused`, the `stop` event, `drain()` | `grokklings/cli.py` |

## Task statuses

| Design doc | In code |
|---|---|
| waiting | `queued` |
| in progress | `in_progress` |
| done | `done` |
| failed | `failed` |
| dropped | `dropped` (a duplicate, with a reference to the first instance) |
| unplaced | `unplaced` |
| looped | `looped` |

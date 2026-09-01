# 0020. An unpriced model is unknown cost, not free

- Status: **accepted**
- Date: 2026-08-27
- Related: [0007](0007-otel-event-names.md), [0019](0019-clones-scale-on-a-hold-time.md), [the third survey](../research/2026-08-before-phases-4-and-6.md)

## Context

Phase 6 asks for accumulated cost and a limit. Two things made it cheap by
the time it was picked up: every model call already writes its token usage
to the journal, and the
[third survey](../research/2026-08-before-phases-4-and-6.md) found that
litellm publishes a maintained per-token price table as one MIT-licensed
JSON file — verified by fetching it, 3365 models including `xai/grok-4.6`.

[0019](0019-clones-scale-on-a-hold-time.md) made this urgent rather than
merely nice: the colony can now grow its own instance counts, so it can
spend faster without anyone touching the config.

## Decision

`grokklings cost` totals the journal's model calls against a **pinned** copy
of the price table. `grokklings cost --refresh` updates that copy. A `[cost]`
section with `limit` and/or `daily_limit` turns the totals into a stop.

**An unpriced model is unknown cost, not zero cost.** This is the decision
the rest hangs off. The natural implementation treats a missing rate as
`0.0` and carries on — and then a colony running an unpriced model reports
spending nothing while spending steadily, and the limit never fires. That
is precisely the failure a limit exists to prevent, and it would look like
everything was fine.

So: unpriced calls are counted and their models named, `Spend.complete` is
false, `grokklings cost` exits non-zero and says the figure is lower than
reality, and **a limit is not enforced against a total known to be
incomplete.** Refusing to enforce is uncomfortable — it means a runaway
unpriced model isn't stopped — but enforcing against a number that is
wrong in an unknown direction is worse, and the warning is loud rather
than swallowed.

**Pinned, not fetched at runtime.** A colony that phones out mid-tick for
prices has a network dependency in its hot path and a spend figure that
moves under it. Refreshing is a thing you do on purpose.

**A limit pauses; it doesn't kill.** `dispatcher.paused` already means "no
new tasks taken, in-flight ones seen through" — the stop control section 12
asks for. Reused rather than reinvented. The check runs before the pause
test, so a colony over budget stays paused instead of resuming next tick.

**A limit with no pinned price table refuses to start.** A limit that
silently isn't enforced is worse than no limit, because it was asked for.

**The daily window is a rolling 24 hours**, not a calendar day, so no
timezone has to be agreed on.

**No new dependency.** `urllib` fetches, `json` reads.
[0001](0001-python-stdlib.md) is untouched — this is the first borrowed
thing that costs nothing to install, because what was borrowed is data
rather than code.

## Consequences

- **A runaway unpriced model is not stopped.** Stated plainly because it's
  the sharp edge: the guard warns on every check and enforces nothing. The
  fix is to price the model — refresh, or add it by hand.
- Prices go stale between refreshes, and nothing nags. A pinned figure that
  is three months old still looks precise.
- Cost is attributed per model and per slot, but not per task, though the
  journal has what's needed for that.
- The guard totals incrementally after the first pass, so a long-running
  colony doesn't re-add its history every tick. That total lives in memory:
  a restart re-reads the journal, which is correct but not free.
- Nothing resumes a paused colony automatically. A daily limit that has
  rolled past still needs a restart. That's a gap, not a design.

## How to undo it

Delete the `[cost]` section: no accounting, no limit, no code change. To
remove it entirely, revert this commit — `grokklings/cost.py` goes, the
`cost` command goes, and the dispatcher loses one check at the top of
`tick`. The pinned `prices.json` is just a file.

## Sources

- `BerriAI/litellm`, `model_prices_and_context_window.json` · MIT ✅ —
  fetched and used, not read about: 3365 models, `xai/grok-4.6` at
  $2e-06 input / $6e-06 output per token. Verified end to end through the
  CLI: four calls of 250k in / 50k out on `grok-4.6` totalled $3.20, and one
  of 100k / 20k on `claude-opus-5` totalled $1.00, both matching hand
  arithmetic.

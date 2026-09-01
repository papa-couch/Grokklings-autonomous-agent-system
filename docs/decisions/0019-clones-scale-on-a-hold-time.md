# 0019. Clones scale on a hold time, not on the current queue

- Status: **accepted**
- Date: 2026-08-27
- Related: [0006](0006-instance-reservation.md), [0014](0014-grok-router.md)

## Context

Phase 3. `SlotRuntime.add_instance()` and `remove_instance()` have existed
since phase 1 with a comment calling them the extension point; nothing ever
called them. `max_instances` was both the starting count and the ceiling,
and the count never moved.

The obvious rule — spawn when the queue is deep, collapse when it isn't —
is the one that doesn't work. Queue depth is spiky by nature. React to the
instantaneous value and a colony spawns on a burst, collapses on the lull a
second later, spawns again on the next burst, and spends its time churning
instances instead of draining work.

## Decision

A `Scaler` runs at the top of each dispatcher tick, before work is handed
out, so a clone spawned on this pass can take a task on it.

**Every decision has to hold.** Pressure must persist for `grow_after`
seconds before a clone appears; idleness for `shrink_after` before one goes
away. The clock resets the moment the condition stops being true, so a
burst that clears spawns nothing.

**The two waits are deliberately asymmetric** — 10 seconds to grow, 60 to
shrink. Being slow to grow costs latency on a real backlog; being slow to
shrink costs an idle instance, which costs nothing. When the two errors are
that different, the thresholds should be too.

**One step at a time, in both directions.** A rule that jumps straight to
the count the current backlog seems to justify overshoots every burst, and
the correction is another spawn-collapse cycle.

**Unrouted work counts toward a single entry slot.** Freshly submitted
tasks aren't routed yet and sit under `(unassigned)` in `queue_depth()`.
Without attributing them, the entry slot — the one actually taking the load
— would never grow, which would make the whole feature useless for the
common case. With several entry slots the router decides later, so guessing
here would grow the wrong slot: those go unattributed, and that's stated
rather than silently approximated.

**`max_instances` becomes the ceiling, and `min_instances` the floor.**
Without a `[scaling]` section a slot still starts at `max_instances` and
keeps them — unchanged behavior. With one, it starts at `min_instances` and
grows toward the ceiling.

Outputs aren't scaled from the queue: an output's load follows whatever the
workers finish, not what's waiting to start.

```toml
[scaling]
min_instances = 1        # what a slot idles at
queue_per_instance = 3   # backlog per instance that justifies another
grow_after = 10.0        # seconds the pressure must hold
shrink_after = 60.0      # seconds idle before giving one back
```

## Consequences

- A colony that scales starts smaller than the same colony that doesn't,
  so the first burst is slower until it grows. `min_instances` is the dial
  for anyone who'd rather pay for idle capacity than for latency.
- Nothing here knows about cost. It scales on queue depth, which is a proxy
  for work, not for money — a slot of expensive model calls scales exactly
  like a cheap one. Cost limits are phase 6.
- The hold times are in wall-clock seconds against the event loop's clock,
  so a colony paused and resumed sees the pause as elapsed time. Harmless
  in both directions: on resume, a real backlog is still a backlog and real
  idleness is still idleness.
- Scaling decisions are journalled with the backlog and the resulting
  count, so a later reader can tell whether the thresholds were sensible.
  Nothing reads them yet.

## What this fixed on the way

Writing `[scaling]` with no keys under it parses to an empty dict, and the
first version read that as "no section" — so a user who asked for the
defaults got nothing, silently. The same trap was already live in
`[routing]` from [0014](0014-grok-router.md). Both now distinguish an
absent section (`None`) from an empty one (`{}`), and the empty one means
what it looks like it means.

## What's left, named honestly

- No cost awareness (above).
- The scaler reacts to queue depth, not to how long tasks are waiting.
  Latency would be the better signal and the journal has the timestamps
  for it; depth is what `queue_depth()` already returns cheaply.
- No global cap across slots: ten slots at their ceilings can be a lot of
  concurrent model calls, and nothing notices the total.

## How to undo it

Delete the `[scaling]` section: the colony returns to static counts with no
code change. To remove it entirely, revert this commit — `scaling.py` goes
away, `SlotRuntime` loses its `start` argument, and the dispatcher loses
one call at the top of `tick`. `add_instance` and `remove_instance` go back
to being an extension point nobody calls.

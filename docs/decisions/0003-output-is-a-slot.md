# 0003. An output is a terminal slot, not a separate mechanism

- Status: accepted
- Date: 2026-08-27

## Context

The design doc describes an output point as "a regular worker, just a final
one." That could be read as a separate delivery layer, or taken literally.

## Decision

Taken literally. An output is a `SlotSpec` with `terminal = true`, and it
goes through the same dispatcher as any worker. A "done" verdict from a
regular worker doesn't finish the task — it sends it to the matching
outputs; "done" from the last output is what finishes it.

## Consequences

Delivery got a timeout, retries with growing backoff, and journal entries
for free: a webhook that fails no longer loses the result.

Several outputs get the task one after another, not all at once — one task
can't be in two places at the same time. The order follows the config's
order.

Hops into an output spend the hop counter just like any other hop: a colony
with a dozen outputs will run into `max_hops`.

## How to undo it

Remove the branch with `_pending_outputs` from `Dispatcher._complete` and
deliver results from a separate loop instead. Output definitions in the
config would still be valid.

# 0009. Phase 4's memory — borrowed, not built

- Status: **proposed**
- Date: 2026-08-27

## Context

Section 8 of the design doc describes two memory tiers and a conflict rule:
don't overwrite, accumulate versions with a timestamp and an author. That's
a description of an established field, not something we invented.

## Proposal

Don't write shared memory ourselves. Bring in something ready-made.

Candidates (Letta, mem0, Zep/Graphiti) are described in
[research/2026-08-ecosystem.md](../research/2026-08-ecosystem.md), each
marked for what's verified and what's taken from survey articles. **This
record doesn't pick a candidate** — that needs a check that hasn't happened
yet.

## What it buys

Section 8 gets closed with someone else's code. The "accumulate versions
with a timestamp and an author" rule is solved more thoroughly in temporal
graphs than we'd manage to build ourselves.

## What it costs

The dependency is heavier than it looks: Letta isn't a library, it's a
platform with a server (verified against the primary source, Apache-2.0
license). That's no longer "install it and run it."

## The check this record deferred — done 2026-08-27

[The third survey](../research/2026-08-before-phases-4-and-6.md) opened all
three candidates. What it found changes the shape of the decision:

- **Graphiti** ✅ (Apache-2.0) states our conflict rule almost verbatim —
  "old facts are invalidated — not deleted... query what was true at any
  point in time." It needs a graph database: Neo4j, FalkorDB, or Neptune.
  Its embedded option (`falkordblite`) removes the server but **requires
  Python 3.12+**, raising this project's floor from 3.11.
- **mem0** ✅ (Apache-2.0) self-hosts as `docker compose up`, and its
  defaults reach for OpenAI embeddings — a second provider in a Grok-first
  colony.
- **Letta** — unchanged: a platform with a server.
- ⚠️ Zep's Community Edition is *reported* deprecated by a comparison
  article and not confirmed against Zep's own repository. Don't repeat it.

**Every candidate needs infrastructure the colony doesn't have.** So the
question is no longer which library but whether the colony will require a
database at all — the same question
[0011](0011-durable-core.md) asks, and the two should probably be answered
together rather than yes here and no there.

A cheaper option deserves weighing first, and this record didn't consider
it: the journal already stores versioned contributions with an author and a
timestamp, `Instance.memory` exists, and `Task.dedup_key` is already the
memory key. A first tier that is one more table beside the journal would
fit the existing shape. It would not close section 8 as thoroughly. That is
a trade-off, not a foregone conclusion, and it is the user's call.

Status stays **proposed**: still nothing in the code, and now a clearer
statement of what adopting would cost.

## How to undo it

Nothing's started yet — there's nothing to undo. There's one seam: the
task's deduplication key, per the design doc, is the same as the shared
memory key.

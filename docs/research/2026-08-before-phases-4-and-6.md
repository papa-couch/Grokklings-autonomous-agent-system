# Third survey: phases 4 and 6 — August 2026

What exists for the two phases still open: shared memory (4) and
observability with cost limits (6). Same question as before phase 5 — what
can be borrowed, and what does borrowing actually cost us.

**How to read the marks** — as in the [first survey](2026-08-ecosystem.md).

- ✅ **verified** — I opened the repository card or the official
  documentation myself, or ran the thing.
- ⚠️ **unverified** — from a comparison article or an aggregator.

One thing to keep in view throughout: every borrowed piece so far
(`openai`, `anthropic`, `cje-eval`, `gepa`) is a Python package behind an
optional extra. Nothing has yet required a *server*.
[0001](../decisions/0001-python-stdlib.md) survives on that distinction,
and phase 4's candidates are where it comes under real pressure.

---

## Phase 4 — shared memory

The design doc's section 8 asks for two memory tiers and one rule: don't
overwrite, accumulate versions with a timestamp and an author.
[0009](../decisions/0009-memory-not-ours.md) proposed borrowing rather than
building, and deliberately picked no candidate pending a check. This is the
check.

### Graphiti ✅ — the closest fit, and it needs a graph database

`github.com/getzep/graphiti` · Apache-2.0

Read off the repository card. Its temporal model is our rule, stated in
their words:

> "When information changes, old facts are invalidated — not deleted. Query
> what's true now, or what was true at any point in time."

Facts carry validity windows: when they became true, and when they were
superseded. That is section 8's conflict rule, solved more thoroughly than
we would.

**What it costs.** A graph database: Neo4j 5.26+, FalkorDB 1.1.2+, or
Amazon Neptune. Kuzu is listed as deprecated and upstream-unmaintained.

**There is an embedded option, with a catch.** FalkorDB ships an embedded
build via the `falkordblite` extra — no separate server. But it **requires
Python 3.12+**, and this project's floor is 3.11. Adopting the
zero-infrastructure path therefore raises our minimum Python version. That
is a small, concrete, checkable cost, and it is the kind that gets
discovered after the decision rather than before it.

### mem0 ✅ — self-hostable, but Docker-shaped

`github.com/mem0ai/mem0` · Apache-2.0

Read off the repository card. Three deployment modes: a library for
prototyping, a self-hosted server, and a cloud platform. Multi-level memory
across user, session, and agent state.

Self-hosting is `docker compose up` — a vector store, plus an LLM and an
embedding model (defaulting to OpenAI's). ⚠️ A comparison article calls the
whole stack self-hostable with a good local story; the repository card
shows Docker rather than a single-process option, and mentions no
SQLite-shaped path. The library mode is described for testing and
prototyping, not as the production shape.

**The embedding dependency matters for us specifically.** mem0's defaults
reach for OpenAI embeddings. A Grok-first colony would be pulling in a
second provider purely for memory.

### Letta — unchanged from the first survey ✅

Apache-2.0, and a platform with a server rather than a library. Recorded in
[0009](../decisions/0009-memory-not-ours.md) already; nothing new found.

### Zep Community Edition ⚠️ — reported deprecated, not confirmed

A comparison article states the Community Edition is deprecated and that
self-hosting now means running Graphiti plus your own graph database.
**The Graphiti repository card doesn't mention a Community Edition at all**,
describing Zep as managed infrastructure built on Graphiti's core. That is
consistent with the claim but does not confirm it. Don't repeat the
deprecation as fact without checking Zep's own repository.

### What this means for phase 4

Every candidate needs infrastructure our colony currently doesn't have.
That's not a reason to build our own — section 8's rule really is solved
better elsewhere — but it moves the decision from "which library" to
"is the colony ready to require a database." That question is
[0011](../decisions/0011-durable-core.md)'s question too, and the answer
should probably be the same in both places rather than yes here and no
there.

A cheaper reading exists and should be considered before adopting anything:
`Instance.memory` already exists, the journal already stores versioned
contributions with an author and a timestamp, and `Task.dedup_key` is
already the memory key per the design doc. A first tier of shared memory
that is a table next to the journal would fit the existing shape, and would
not close section 8 as thoroughly as Graphiti would. That's a real
trade-off, not a foregone conclusion.

---

## Phase 6 — observability and cost limits

### The price table is the useful borrow ✅ — verified by running it

`BerriAI/litellm` · MIT · the file
`model_prices_and_context_window.json`

The annoying half of cost tracking isn't the arithmetic — we already
journal `input_tokens` and `output_tokens` on every model call as a
`worker_note`. It's keeping per-model prices current. That file is a
maintained table of exactly that, fetchable as one JSON with no dependency
on litellm itself.

**Verified by fetching it, not by reading about it:** 1.9 MB, HTTP 200,
3365 models, 36 of them under the `xai` provider — including our default:

| model | input / token | output / token | context |
|---|---|---|---|
| `xai/grok-4.6` | $2e-06 | $6e-06 | 500k |
| `xai/grok-4` | $3e-06 | $1.5e-05 | 256k |

So a day of 1M input and 200k output tokens on `grok-4.6` is about **$3.20**.
That is the whole of cost tracking for us: a fetch, a lookup, and a
multiply — plus a decision about what to do when a limit is hit, which is
ours and not borrowable.

⚠️ Fetching prices at runtime means a network call and a moving number. A
pinned local copy that a command refreshes is the safer shape, and it makes
"why did the cost estimate change" answerable.

### The observability platforms are all heavier than this colony

| | License | What it needs | Mark |
|---|---|---|---|
| **Arize Phoenix** | **Elastic License 2.0** | `pip install`, `phoenix serve` — runs locally, no external DB for basic use | ✅ |
| **Langfuse** | MIT (core) | ⚠️ Postgres + ClickHouse + Redis + S3 in v3 | ⚠️ |
| **OpenLLMetry** | ⚠️ | an OTel collector and somewhere to send spans | ⚠️ |

**Phoenix is not open source in the usual sense, whatever the comparison
articles say.** It's Elastic License 2.0 — source-available, with a
restriction on offering it as a managed service. Every other thing this
project borrows is MIT or Apache-2.0. That's not disqualifying for private
use, and it is a difference worth stating rather than discovering later.

Phoenix is also the only one of the three that plausibly fits a one-machine
colony: a `pip install` and a `serve`, no external database for basic
operation. ⚠️ "No external database for basic operation" is the card's
claim; what it degrades to at volume isn't checked.

Langfuse's v3 architecture is four services. For a colony whose whole point
is that `git clone` and `python -m grokklings run` is the setup, that is the
wrong shape regardless of its merits at scale.

### Where this lands against 0007

[0007](../decisions/0007-otel-event-names.md) proposed renaming journal
events onto the OpenTelemetry GenAI convention, and it is still
**proposed**. Everything above reinforces why: Phoenix, Langfuse, and
OpenLLMetry all ingest OpenTelemetry. If our journal spoke that vocabulary,
plugging any of them in would be an exporter rather than an integration —
and *not* choosing one of them would cost nothing. That makes 0007 the
cheapest thing in phase 6 and the one that keeps the most options open,
which is a better argument for it than the one 0007 currently makes for
itself.

---

## What this survey changes

| | Before | After |
|---|---|---|
| Phase 4 candidates | three, mostly ⚠️ | **all three need infrastructure**; Graphiti's temporal model is an exact fit, its embedded option needs Python 3.12+ |
| mem0's local story | ⚠️ "self-hostable" | Docker-shaped ✅, and reaches for OpenAI embeddings |
| Zep CE deprecated | not known | ⚠️ reported, **not confirmed** — don't repeat it |
| Cost tracking | "not done" | a maintained price table exists ✅, covers `xai/grok-4.6`; the arithmetic is ours and small |
| Phoenix | ⚠️ "open source" | **Elastic License 2.0** ✅ |
| 0007's priority | one item among several | the cheapest phase-6 move, and the one that keeps every dashboard option open |

Nothing adopted, nothing decided. Phase 4 in particular now needs a
decision this survey can't make for you: whether the colony is willing to
require a database.

# 0007. The journal speaks OpenTelemetry — by translation, not by renaming

- Status: **accepted**, with a change of mechanism — see below
- Date: 2026-08-27
- Related: [research/2026-08-ecosystem.md](../research/2026-08-ecosystem.md)

## Context

Phase 6 of the design doc covers live state, a dashboard, accumulated cost.
Right now journal events are named our own way: `task_assigned`,
`worker_result`, `task_routed`. Other people's tools can't read a journal
like that, and a dashboard would have to be built from scratch.

OpenTelemetry has a convention for agentic systems. Verified against the
primary source: operations `create_agent`, `invoke_agent`,
`invoke_workflow`, `plan`, `execute_tool`; required attributes
`gen_ai.operation.name` and `gen_ai.provider.name`; recommended
`gen_ai.agent.name`, `gen_ai.agent.id`, `gen_ai.request.model`,
`gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`.

## Proposal

Name journal events on this schema, and keep the names in one place instead
of scattered through the code.

## What it buys

The dashboard stops being our problem: a journal shaped this way can be read
by Langfuse, Phoenix, Datadog. "Accumulated cost" gets computed from
`gen_ai.usage.*` instead of a separate counter we'd have to maintain
ourselves.

## What it costs

**The scope, measured, not guessed.** Recording actual code, not a time
estimate: 28 call sites across 8 files, 24 distinct event names. Of those,
roughly a third map cleanly onto the convention (`task_assigned` ->
`invoke_agent`, `worker_result` -> the same span's close); the rest —
`task_duplicate`, `task_unplaced`, `task_looped`, `instance_spawned`,
`step_replayed`, and a few others — have nowhere to go in it. So this isn't
a rename; it's deciding which events speak the shared vocabulary and which
stay ours, and how one journal holds both without becoming a mess. An
earlier pass at this estimated it as roughly half a day of work — that
number wasn't grounded in anything and shouldn't be trusted; this paragraph
replaces it.

**The agentic part of the convention is at Development status — it isn't
stable.** That's a direct quote from the spec, not caution on my part.
Operation and attribute names can change, and then we'd have to change ours
too. Hence the requirement to keep names in one module: a rename should be a
one-file edit.

## How to verify it paid off

Stand up Langfuse or Phoenix on a test colony's journal. If a task's history
is visible without writing glue code, it paid off.

## How to undo it

Bring back the old names, in that same one module. Data already in older
databases keeps its old names — that would need either a migration or code
that reads both schemas.

## A stronger argument than this record makes, found 2026-08-27

[The third survey](../research/2026-08-before-phases-4-and-6.md) looked at
what phase 6 would plug a dashboard into. Phoenix, Langfuse and OpenLLMetry
all ingest OpenTelemetry. If the journal spoke that vocabulary, adopting
any of them would be an exporter rather than an integration — and adopting
*none* of them would cost nothing.

That makes this the cheapest move in phase 6 and the one that keeps the
most options open, which is a better reason to do it than "the convention
exists." The scope in this record is unchanged; only the case for it is
stronger.

(Also worth knowing before picking a dashboard: **Arize Phoenix is Elastic
License 2.0** ✅, not MIT or Apache-2.0 like everything else this project
borrows.)

---

## Accepted 2026-08-27, and the rename didn't happen

**What got built is a translation layer, not a rename.** `grokklings/otel.py`
holds the convention's operation and attribute names in one place, maps five
of our events onto them, and leaves the rest alone. `grokklings export`
writes the mapped ones as OTLP-shaped JSON lines.

The proposal above said "name journal events on this schema." Doing that
literally would have been wrong, for reasons this record had already half
identified when it wrote *"this isn't a rename; it's deciding which events
speak the shared vocabulary and which stay ours"*:

- **Most of our events have no equivalent, and renaming loses meaning.**
  `task_unplaced`, `task_looped`, `step_replayed`, `proposal_made`,
  `scaling_decision` say something this design means and the convention
  doesn't. Renaming them to the nearest OTel word would claim a meaning
  that isn't there.
- **A rename breaks every journal already on disk.** This record's own "how
  to undo it" worried about needing "a migration or code that reads both
  schemas." Translation avoids that: stored data never changes.
- **The convention is at Development status** — confirmed by reading the
  spec directly, not assumed. When names change, one file changes and no
  data does.
- **Adopting no dashboard costs nothing.** Nothing in the colony imports
  `otel.py`; it reads the journal from outside, like the brain does.

### The measured scope, remeasured

This record said 28 call sites across 8 files, 24 distinct names, "roughly a
third" mapping cleanly. Counted again by walking the AST: **38 call sites
across 12 files, 33 distinct names.** The project grew routing, scoring,
proposals, calibration, optimization and scaling since.

And the fraction was optimistic: **five of the 33 translate**, not a third.
Everything the project added since is its own vocabulary. That is not a
disappointing result — it's the shape of the thing. What maps:

| ours | convention |
|---|---|
| `task_assigned` | `invoke_agent` |
| `worker_result` | `invoke_agent`, plus `error.type` on a deliberate `fail` |
| `worker_note` carrying a model and usage | `chat`, with `gen_ai.usage.*` |
| `instance_spawned` | `create_agent` |
| `slot_picked` | `plan` |

The other 28 are listed by name in `OURS_ALONE`, so the split reads as a
decision rather than as leftovers — and a test walks the AST to fail if an
event is added and accounted for in neither list.

### Two details that were checked rather than guessed

- **xAI's registered provider name is `x_ai`, not `xai`** — read from
  `docs/registry/attributes/gen-ai.md`. It's exactly the kind of thing that
  gets guessed wrong and produces spans that look conformant.
- **An unknown model gets no provider at all, not a default.**
  `gen_ai.provider.name` is required and has a registry of allowed values;
  inventing one is worse than omitting it.

### What this deliberately isn't

`export` emits OTLP-*shaped* records: no collector, no protobuf, no gRPC.
Calling it an exporter would oversell it. Whether a dashboard ingests the
file without glue is the check this record set for itself, and it hasn't
been run — nobody has stood up Phoenix or Langfuse against it.

Cost is now one short step away rather than a separate counter, exactly as
this record predicted: `gen_ai.usage.*` is in the spans, and the third
survey found a maintained price table. Nothing computes cost yet.

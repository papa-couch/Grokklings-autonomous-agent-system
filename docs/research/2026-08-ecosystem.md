# Ecosystem survey, August 2026

What other people have already built that overlaps with the design doc.
A one-time, dated survey: the ecosystem moves, and some of this will be
stale within six months.

**How to read the marks.**

- ✅ **verified** — I opened the repository or the official documentation
  myself and read what's written here.
- ⚠️ **unverified** — taken from a survey article, not checked against the
  primary source. Don't base a decision on a claim like this without checking it.

---

## Our vocabulary vs. the industry's

Almost every term in the design doc has an established name elsewhere. This
isn't cosmetic: those names are what you search for to find ready-made code,
and not knowing the name is the main reason to end up writing your own.

| Ours | Industry term | Mark |
|---|---|---|
| A task never disappears | durable execution | ✅ |
| A queue in the database + a lease | queue table + lease | ✅ |
| Deduplication key | idempotency key | ✅ DBOS: `SetWorkflowID` |
| Hop counter | recursion limit | ✅ LangGraph: `recursion_limit` |
| A "next: slot" verdict | handoff, `Command(goto=…)` | ✅ LangGraph |
| The "may hand off to" list | handoff list | ⚠️ OpenAI Agents SDK |
| Results accumulate | reducer | ✅ LangGraph |
| A slot's instance cap | worker slots | ✅ Hatchet, the exact same term |
| Picking a slot by description | semantic routing | ✅ |
| The colony's shared memory | long-term memory | ⚠️ |
| The unplaced pile | dead letter queue | ⚠️ ours means something different, see below |
| The journal | execution trace | ✅ |
| The brain edits context | reflective prompt optimization | ✅ GEPA |
| The brain proposes a new role | — | found nothing ready-made |

---

## Task infrastructure — phases 1, 3, 7

### Hatchet ✅

`github.com/hatchet-dev/hatchet` · MIT · Postgres · SDKs: Python, TS, Go, Ruby

Read straight off the repository's card: background tasks as ordinary
functions; fair scheduling via concurrency policies keyed on a dynamic
value; **worker slots** — "so a worker never takes on more than it can
handle"; rate limits, including dynamic ones; retries with exponential
backoff; durable tasks and durable sleep; DAGs; event- and webhook-triggered
runs; routing by worker labels and affinity; a web UI with alerting;
OpenTelemetry and Prometheus.

Covers our phases 1, 3, and most of 11.

### DBOS Transact ✅

`github.com/dbos-inc/dbos-transact-py` · MIT · a library, not a service

Read straight off the repository's card: `@DBOS.workflow()` and
`@DBOS.step()`, state checkpoints in the database, recovery from the last
completed step after a crash; queues with concurrency limits, timeouts, and
rate limits; `@DBOS.scheduled()` with cron syntax; idempotency via
`SetWorkflowID(event_id)` — "run exactly once."

⚠️ **SQLite: unconfirmed.** A survey article claimed DBOS supports SQLite.
The repository itself lists SQLite in its tags, but the README only spells
out Postgres. This is an open question, and it decides the fate of
[0011](../decisions/0011-durable-core.md).

### durable-execution-the-hard-way ✅

`github.com/hatchet-dev/durable-execution-the-hard-way` · a tutorial · Postgres + Go

Not a library — a lesson-by-lesson walkthrough: a simple queue ->
concurrency limits -> queue improvements -> a durable event log ->
tracking non-determinism -> durable tasks.

The main thing worth taking from it regardless of which engine gets picked:
three distinct operations that we've collapsed into one:

| Operation | What it does |
|---|---|
| retry | continues, keeping the history |
| replay | resets the history and starts over |
| fork | rolls the history back to a chosen point |

We only have the first.

---

## Routing — phase 2

### semantic-router ✅

`github.com/aurelio-labs/semantic-router` · MIT · 3.8k stars

Read straight off the repository's card: the routing decision is made by
comparing vectors, not by calling a model; a route is a `Route` with a name
and a list of example phrases; a fully local mode via `HuggingFaceEncoder`
and `LlamaCppLLM` (`pip install "semantic-router[local]"`), no API call needed.

### LangGraph ✅

Verified in the official documentation (`docs.langchain.com`):

- **reducer** — set via `Annotated` on a state key; the function takes the
  current value and a node's update and returns the merged result. This
  generalizes our "results accumulate, never overwritten" for the case where
  branches run in parallel and merge back together. We don't have this.
- **`recursion_limit`** — a cap on the number of steps; exceeding it raises
  `GraphRecursionError`. Our hop counter.
- **`Command(update=…, goto=…)`** — a node changes state and picks the next
  step in one move. Our "next" verdict.

### OpenAI Agents SDK ⚠️

From survey articles, not checked against the primary source: an agent
declares a list of who it may hand off to (our `can_route_to`); a topology
of three to seven agents is called the practical ceiling, past which picking
a receiver starts to go wrong.

If confirmed, this is a hard constraint for the brain: roles can't be grown
without a cap. **Verify this before relying on it in phase 5.**

> **Checked 2026-08-27: the number isn't there.** Neither the handoffs page
> nor the orchestration guide states any ceiling. The claim was a survey
> article's, and it doesn't survive contact with the primary source — see
> [2026-08-before-phase-5.md](2026-08-before-phase-5.md#1-the-3-to-7-ceiling-doesnt-exist--closes-open-question-2).

---

## Memory — phase 4

| Project | What's known | Mark |
|---|---|---|
| **Letta** | Apache-2.0. "A platform for stateful agents." Not just a library: installs and runs as a server, has a cloud option | ✅ license and character |
| **Letta** | a core / recall / archival hierarchy | ⚠️ not confirmed on the repository's card |
| **mem0** | a hybrid of vectors, a graph, and key-value; automatic fact extraction; the widest range of integrations | ⚠️ |
| **Zep / Graphiti** | a temporal knowledge graph: stores how facts changed over time | ⚠️ |

A temporal graph is exactly our "conflict: don't overwrite, accumulate
versions with a timestamp and an author." But the ⚠️ mark means the
repositories need opening and checking before a pick gets made.

---

## Observability — phase 6

### OpenTelemetry GenAI ✅

Verified against the spec (`open-telemetry/semantic-conventions-genai`):

- operations: `create_agent`, `invoke_agent`, `invoke_workflow`, `plan`,
  `execute_tool`;
- required attributes: `gen_ai.operation.name`, `gen_ai.provider.name`;
- recommended: `gen_ai.agent.name`, `gen_ai.agent.id`, `gen_ai.request.model`,
  `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`;
- **the agent part of the spec is at Development status, not stable.**

That last point matters: it doesn't cancel the proposal in
[0007](../decisions/0007-otel-event-names.md), but it does mean event names
need to live in one place.

---

## The brain — phase 5

### GEPA ✅

`github.com/gepa-ai/gepa` · MIT

Read straight off the repository's card: optimizes any text parameter —
prompts, code, configs — through model reflection and evolutionary search.
Plugs in via a `GEPAAdapter` interface, with ready-made adapters for DSPy,
LangChain, RAG, and MCP.

**Needs three things:** a quality metric, a training/validation set (as few
as three examples is enough), and an evaluation function that returns a
score along with actionable feedback — an error message, diagnostics.

⚠️ **Correction to what I said the first time.** I originally said our
journal already is the traces it needs, so half the brain is basically
built. That's wrong: GEPA collects its own traces during evaluation, and
without a metric and a training set it doesn't run at all. Before adopting
GEPA, we need a result-quality function — an answer to "what counts as
success" from the brain's context. Details in
[0010](../decisions/0010-brain-scope.md).

The claimed results (35x fewer runs than reinforcement learning) come from
the README and haven't been checked by anyone outside the project.

> **Corrected 2026-08-27.** They have been now: the paper (arXiv 2507.19457)
> is accepted at **ICLR 2026 as an Oral**. Peer review isn't replication,
> but this is no longer only the project's own claim. And the prerequisite
> named above — a result-quality function — exists as of
> [0015](../decisions/0015-success-is-the-users-answer.md), so GEPA went
> from blocked to adoptable. See
> [2026-08-before-phase-5.md](2026-08-before-phase-5.md#2-gepa--the-brains-context-editing).

---

## What nobody else has

> **Weakened 2026-08-27.** A second survey done before phase 5 found
> `EvoAgentX` (workflows generated from natural-language goals, a
> self-evolving engine, optional human-in-the-loop) and
> `BerriAI/self-improving-agent` (propose a diff, a human approves, a draft
> PR opens). Claims 1 and 3 below overstate how alone this design is —
> about one and a half of the three survive. The corrected version, with
> what still looks distinct and why that's stated cautiously, is in
> [2026-08-before-phase-5.md](2026-08-before-phase-5.md#4-self-evolving-agent-systems--closer-to-us-than-the-first-survey-thought).
> The text below is left as it was written.

Three things, and they're exactly what keeps this design doc from being a
retelling of someone else's work.

1. **A slot is described in words, in the config, not in code.** Everywhere
   else, an agent is an object in a program. Here, it's a record with a
   name, a description, a context, and handoff permissions. From that it
   follows that the colony is changed by the user, not the developer, and
   that the brain can propose a role in the same form a person would write
   it in.
2. **"Not mine" as a signal to grow.** Technically the unplaced pile is a
   dead letter queue. But there it's a graveyard for dead tasks; here it's
   an input for the brain and a case for a new role. The difference isn't
   in the code — it's in who looks at it, and why.
3. **Growth by consent.** Self-optimizing systems exist. Systems that
   propose a new worker to their owner, explain why the existing ones can't
   handle it, and report back on whether it paid off — none turned up.

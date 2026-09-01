# Open questions

Everything still unresolved. Each question notes how to settle it, so it
doesn't have to be worked out again from scratch.

## 1. Does DBOS run on SQLite

**Why it matters.** Decides the fate of
[0011](decisions/0011-durable-core.md). If yes, the core could be swapped for
a ready-made engine without losing how simple it is to run, and our
`journal.py` honestly becomes obsolete. If no, the swap drags Postgres along
with it, and the question is closed until some other reason comes up.

**What's known.** SQLite is listed in the repository's tags, but the README
only spells out Postgres. A survey article claimed otherwise — that didn't
check out.

**How to settle it.** Install `dbos`, try connecting it to SQLite, run their
own example. Half an hour.

## 2. Is the limit really three to seven slots — **closed 2026-08-27**

Moved to the closed section below.

## 3. What counts as success — **closed 2026-08-27**

Moved to the closed section below.

## 4. Neither model-backed worker has run for real

**Why it matters.** Both `grokklings/handlers/grok.py` (the primary backend)
and `grokklings/handlers/anthropic.py` are written but have never once run
against the real API. There is no key for either in this environment.

**Narrowed 2026-08-28.** Both SDKs are installable here even without a key,
which splits this question in two. The half that needed no key is closed:
`tests/test_sdk_contract.py` binds the keywords our production code sends
against the real `create` signatures and hands our code responses built
from the SDKs' own response models. Both backends pass — every parameter
we send exists, including the newer `thinking`, `output_config`, `betas`
and `fallbacks` on the Anthropic side, and every response field we read is
real. So the Anthropic backend is no longer untested, and a wrong keyword
is no longer invisible.

What is still open is everything only a server can answer: whether xAI
accepts a request its SDK accepts, whether a real Grok returns JSON
matching `VERDICT_SCHEMA`, and how auth, rate-limit and model-name errors
actually arrive. `strict: true` structured outputs in particular are a
server-side promise; nothing local can check it.

**How to settle it.** For Grok: install `pip install 'grokklings[grok]'`, set
`XAI_API_KEY`, run `examples/watcher.toml` on a single task. For Claude:
`pip install 'grokklings[anthropic]'`, `ANTHROPIC_API_KEY`, swap the example's
`handler` to `grokklings.handlers.anthropic:handle`. Any errors in either
request's shape will show up immediately.

## 5. A synchronous worker doesn't get interrupted by its timeout

**Why it matters.** An async worker is cut off by `asyncio.wait_for`; a
regular function keeps running to completion in its own thread — there's
nothing to interrupt it with. The task honestly goes to retry, but the work
can end up duplicated.

**Options.** Either leave it as is and require long-running workers to be
coroutines, or run synchronous ones in separate processes that can be
killed. The second option costs more and drags in task serialization.

## 6. The throughput ceiling — **measured 2026-09-01**

**Why it mattered.** One of the three thresholds for revisiting
[0011](decisions/0011-durable-core.md) is "tasks stop fitting on one
machine," and nobody knew where that line sat.

**Measured**, two slots, 16 instances each, a no-op stdlib worker, so this
is the machinery's own ceiling with no model in the way:

| | rate |
|---|---|
| submit (intake + enqueue) | ~4,100–4,200 tasks/s |
| end to end (2,000 tasks) | ~700 tasks/s |
| end to end (6,000 tasks) | ~600 tasks/s |

**The wall is SQLite's commit rate, not the event loop.** Measured
directly against the journal: `record` ~17,700/s, `enqueue` ~12,300/s,
`claim` ~20,000/s. A task costs roughly a dozen committed writes across
its life, which puts ~600–700 tasks/s squarely at that ceiling. The mild
decay from 700 to 600 across 3× the load is the tables growing, not a
cliff.

**What this settles.** For 0011's threshold: one machine holds until
somewhere around half a million tasks a day, sustained. It also says the
ceiling is far away for the colony this project is actually for — a
model-backed slot spends seconds per task on the network, so a colony
doing real work hits its provider's rate limit thousands of times before
it comes near 600/s. Reconsider the durable core for the other two
thresholds in 0011, not this one.

**Not measured:** concurrent writers (sources in their own threads under
real load), and behaviour on a network filesystem, where SQLite's write
rate is a different number entirely.

## 7. Does the router actually pick the right slot

**Why it matters.** [0014](decisions/0014-grok-router.md) put slot-picking
in Grok's hands, and it's covered by tests — but every one of those tests
answers "does the machinery work," not "does it choose well." A router that
confidently picks the wrong slot is worse than no router: the task doesn't
land on the unplaced pile where someone would notice it, it gets quietly
processed by the wrong worker. Nothing in the colony would flag that today.

**How to settle it.** [0008](decisions/0008-semantic-routing.md) already
specified the test and it carries over unchanged: build a set of tasks with
a known correct slot, set the bar *before* measuring, then measure the hit
rate. Needs a key and a colony with several genuinely distinct entry slots.
Worth measuring the honest-`null` rate separately — a router that refuses
when unsure is behaving correctly, and lumping that in with misses would
hide the difference.

## 8. Does the evaluator judge the way the user meant

**Why it matters.** [0015](decisions/0015-success-is-the-users-answer.md)
put the definition of success in the user's hands, and Grok applies it. Two
different things can go wrong and they need telling apart: criteria written
too vaguely to act on, and criteria written well but applied badly. The
first is the user's to fix, the second is ours — and right now nothing
distinguishes them.

The stakes are higher here than for the router. A misrouted task usually
shows up as a bad result somewhere. A miscalibrated judge is invisible: it
produces confident numbers, and everything the brain later decides rests on
them.

**How to settle it.** Score a set of finished tasks the user has already
judged by hand, and compare. Disagreements are the interesting part, not
the average — go through them one by one and decide, for each, whether the
criteria were unclear or the judge was wrong. Worth doing before any of the
brain's proposals get acted on automatically.

**The tooling exists now, the measurement doesn't** — `grokklings calibrate`
([0017](decisions/0017-calibrate-the-judge.md)) wraps `cimo-labs/cje` and
does the whole thing bar one step:

```bash
grokklings calibrate --export sheet.jsonl -n 30   # a random sample, judge's score hidden
grokklings calibrate --labels sheet.jsonl         # how far the judge is from you
```

Verified working against a judge planted 0.2 high on synthetic data: it
recovered the true mean and reported the drift. **What it can't do is
supply the labels.** Somebody has to judge thirty tasks by hand, and that
is the whole point — there is no version of this question that gets
answered without a person answering it.

So this question stays open, but its remaining content is now a task rather
than a design problem: run the export, label the sheet, run the
calibration.

**A specific risk found while surveying:** our evaluator judges Grok's
output with Grok, which is the setup for **self-preference bias** — a
judge rating its own model's output higher. It's a named, tooled-for
phenomenon (`llm-judge-calibrator` detects it alongside position and
verbosity bias). ⚠️ How much it matters when a model judges another
instance of itself rather than a competitor isn't established. The cheap
mitigation, if it does: `[success]` already takes its own `model` key, so
judging can be pointed at a different model than the workers use — a
documentation change and a test, not an architecture change.

## 9. Will the colony require a database — the real phase 4 question

**Why it matters.** [0009](decisions/0009-memory-not-ours.md) said "don't
write memory ourselves" and deliberately picked no candidate. The
[third survey](research/2026-08-before-phases-4-and-6.md) did the checking
that record deferred, and the answer changed the question.

**What's known now** — all ✅ against repository cards:

- **Graphiti** (Apache-2.0) states our conflict rule almost verbatim: old
  facts are invalidated rather than deleted, with validity windows. Needs
  Neo4j, FalkorDB or Neptune. Its embedded option removes the server but
  **requires Python 3.12+**, raising this project's floor from 3.11.
- **mem0** (Apache-2.0) self-hosts as `docker compose up`, and its defaults
  reach for OpenAI embeddings — a second provider in a Grok-first colony.
- **Letta** is a platform with a server, not a library.
- ⚠️ Zep's Community Edition is *reported* deprecated by a comparison
  article, not confirmed against Zep's own repository.

**So the question is no longer which library.** Every candidate needs
infrastructure this colony doesn't have, so what has to be settled first is
whether the colony will require a database at all — the same question
[0011](decisions/0011-durable-core.md) asks about the core, and the two
should be answered together rather than yes here and no there.

**How to settle it.** Not by more research: this one is the owner's call.
The cheaper alternative deserves weighing in the same breath — the journal
already stores versioned contributions with an author and a timestamp,
`Instance.memory` exists, and `Task.dedup_key` is already the memory key.
A first tier that is one more table beside the journal would fit the
existing shape and would not close section 8 as thoroughly.

## 10. A typo in a `[[source]]` option is swallowed

**Why it matters.** `build_source` wraps its factory call in a
`try/except TypeError` that reports which `[[source]]` block is wrong. The
guard is right and it cannot fire: every builtin source ends its signature
in `**options`, so an unknown key binds silently. Writing `prot = 9001`
instead of `port` starts a webhook on the default 8099 with nothing said,
and the first symptom is a feed that never delivers.

Found while writing `tests/test_colony.py`; the current behaviour is pinned
by a test that documents it rather than endorses it.

**Why it isn't just fixed.** Dropping `**options` from the builtin sources
would turn a silently-ignored key into a startup failure. That is almost
certainly the right trade — it is the same reasoning as
[0020](decisions/0020-cost-and-limits.md), where a limit that silently
isn't enforced was judged worse than no limit — but it is a behaviour
change to a public surface, and someone's working config could stop
booting. That makes it a decision record, not a patch.

**How to settle it.** The owner's call, and cheap either way. If strict:
name the accepted options on each builtin source, keep `**options` on the
`Source` base for user-written classes, and the existing guard starts
working with no other change. The same question applies to `[[slot]]`
options, which are passed through to workers on purpose and probably
should stay open — so the answer may well be "strict for sources, open for
slots" rather than one rule for both.


---

# Closed

Kept here so something already settled doesn't have to be worked out again.

## Step-level idempotency — closed 2026-08-27

A worker that reached out to the outside world and crashed before returning
a verdict would go out there again on retry. Closed with the `ctx.once()`
primitive — [0012](decisions/0012-step-idempotency.md).

Not closed completely, and that's recorded honestly: `once()` closes the gap
between "the step succeeded" and "the verdict was recorded." The gap inside
the call itself — the service already accepted the request, and we crashed
before hearing back — isn't closed by anything without the receiving side's
cooperation. `ctx.idempotency_key()` exists for that case, but the service
has to honor it.

## The three-to-seven slot ceiling — closed 2026-08-27

It doesn't exist. Checked both primary sources the question named — the
Agents SDK handoffs page and the orchestration guide — and neither states a
limit or a ceiling of any kind
([survey](research/2026-08-before-phase-5.md#1-the-3-to-7-ceiling-doesnt-exist--closes-open-question-2)).
The claim came from a survey article and shouldn't have outlived the check.

Settled the way the question itself specified: not there, so treat it as
unverified and don't rely on it. **The brain gets no hard cap on this
authority.** What the primary source does give is a criterion instead of a
number — add a specialist only when it "materially improves capability
isolation, policy isolation, prompt clarity, or trace legibility," and
splitting early costs more than it buys. That's a better bar for the
brain's proposals than a threshold would have been.

## What counts as success — closed 2026-08-27

Blocked phase 5 from the start, and not on code: the brain can't tell "this
got better" from "this got worse" without it
([0010](decisions/0010-brain-scope.md)).

Closed by the answer being that there isn't one answer. Success is
individual — different people, different tasks, different definitions — so
the colony doesn't define it: the user writes their criteria in a
`[success]` section, and the colony applies them
([0015](decisions/0015-success-is-the-users-answer.md)).

What this does *not* close: whether a given user's criteria are good, and
whether the evaluator applies them the way that user meant. The first is
theirs; the second is question 8 above. 0010's warning survives intact —
criteria written badly will have the brain diligently make the colony
worse. The change is that they're now visible in the config, where they can
be argued with.

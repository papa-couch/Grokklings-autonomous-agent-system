# Changelog

Format: what changed and why. Decisions live in
[docs/decisions/](docs/decisions/); this is just the timeline.

## 2026-09-02 — the repository is renamed, and the links follow

`Grokklings-automate-system` became `Grokklings-autonomous-agent-system`.
GitHub redirects the old address, so nothing was broken — but a redirect
is not a correct link, and four places in the tree still named the old
repository.

Three of them were `[project.urls]` in `pyproject.toml`: Source, Changelog
and Decisions. Those are what PyPI and `pip show` print, so they are the
project's public address whether or not anyone clicks them.

The fourth mattered more. The README's install block said `git clone …/
grokklings-automate-system` followed by `cd grokklings-automate-system`,
and that pair is the first thing a new reader runs. The clone would have
survived on the redirect; the `cd` would have worked too, since git names
the directory from the URL it was given. Both would have quietly left
someone in a folder named after a repository that no longer exists.

`docs/publishing.md` is rewritten rather than patched. Two of its sections
were about work already finished — the merge into `main`, and a note
weighing whether to rename — so the file had started describing the past
instead of what is left. It is now four steps, all of them still to do.

The description it carries is rewritten too. It used to open on
"Autonomous colony of Grok-backed agents", which the new repository name
now says by itself; a description that repeats the name spends its first
and most visible characters on nothing. It leads with the shape of the
work instead. One topic added: `autonomous-agents`, which the colony's own
run loop backs.

## 2026-09-01 — the actions move to Node 24

`actions/checkout` and `actions/setup-python` both go from the versions
CI shipped with to `@v7`. GitHub had deprecated Node 20 and was already
forcing both onto Node 24, warning on every job.

Verified rather than guessed, since bumping three majors at once invites
a breaking change: v7 is a real release on both (`checkout` v7.0.1,
`setup-python` v7.0.0), both declare `runs.using: node24`, which is what
the warning asks for, and the only input this workflow passes —
`setup-python`'s `python-version` — is still there. `checkout` is called
with no inputs at all, so its surface cannot break us.

Pinned to the floating major, matching what was there before: patches
arrive without a commit here.

## 2026-09-01 — CI's first run, and what it found

The workflow went green on its first run — five jobs, every step, 362
tests. Reading the logs rather than the badge turned up one defect it had
surfaced on its own.

### Fixed

- **A colony that refuses to start leaked its database handle.**
  `Colony.__init__` opens the journal before anything that can refuse, and
  a `[cost]` limit with no price table is *meant* to stop the colony
  starting ([0020](docs/decisions/0020-cost-and-limits.md)). That
  exception escaped without closing the journal, so the handle leaked and
  the WAL files stayed behind — once per attempt, which means a supervisor
  retrying a bad config leaks steadily.

  Python 3.13 reports this as a `ResourceWarning: unclosed database`; 3.11
  says nothing at all, which is why nobody had seen it. Having 3.13 in the
  matrix is what made it visible on the very first run.

### Confirmed from the logs, not the badge

- The extras job ran all 362 with **no skips** — the check that a green
  tick isn't hiding 24 tests that never ran.
- It exercised the real libraries: GEPA's engine actually iterated and
  proposed a rewrite, CJE printed its own `REFUSE-LEVEL` coverage badge.
- **The SDK contract tests passed against openai 3.6.0**, a newer release
  than the 3.5.0 they were written and verified against locally. That is
  the job earning its place: our request shapes were checked against an
  SDK nobody here had seen.
- `stdlib only` imported all 29 modules on 3.11, 3.12 and 3.13 with
  nothing third-party reaching `sys.modules`.

### Still to do

`actions/checkout@v4` and `actions/setup-python@v5` target Node 20, which
GitHub has deprecated and is now forcing onto Node 24. It warns rather
than fails today; the actions need a major-version bump before that
becomes an error.

363 tests.

## 2026-09-01 — CI, and the two tests that never could have passed

362 tests and nothing ran them but whoever was at a keyboard.
[0023](docs/decisions/0023-ci-checks-both-promises.md).

### Fixed

- **The suite did not pass on a bare install.** Two tests in
  `test_optimize.py` errored on a clean virtual environment with no
  extras — which is exactly what a new contributor gets. `optimize_context`
  looked for GEPA *before* it validated its arguments, so the two tests
  covering its guards could not run without the optional dependency they
  were meant to be independent of. The check now comes after the
  validation, which is also the better error: telling someone to install a
  package and letting them find out afterwards that their call was wrong
  anyway wastes their time twice.

### Added

Three jobs, on every push and pull request, because this project makes two
promises and one job would only check the weaker one.

- **`stdlib-only`** on Python 3.11, 3.12 and 3.13 — installs the package
  with no extras and runs the suite. First it imports every module and
  asserts none of `openai`, `anthropic`, `cje`, `gepa` or `numpy` reached
  `sys.modules`, which catches a lazy import that has quietly moved to the
  top of a file. [0001](docs/decisions/0001-python-stdlib.md) stops being
  a claim and becomes a check.
- **`with-extras`** on 3.11 — installs everything and **fails if any test
  skips at all.** A skip here doesn't mean "not applicable", it means a
  dependency didn't install and the SDK contract tests never ran. Green
  and unchecked is worse than red.
- **`lint`** — pyflakes, plus the documentation link check the audits had
  been running by hand.

No secrets, and none wanted: an API key in CI would make every push cost
money and every fork a risk. That is also CI's ceiling here — the
real-API question stays open by design.

### Checked before shipping, in both directions

The bare environment passes on all three Python versions with 24 skips;
with every extra it passes with none. The lint step passes clean and fails
on an introduced dead import. The leak check fails when a third-party
import is moved to module level.

The lint step's first draft had the opposite bug and would have failed
precisely when the tree was clean: `grep -v` exits non-zero when it finds
nothing, and the pipeline read that as an error.

## 2026-09-01 — a licence, so the repository can be used

The review found the repository had no licence at all, which is not a
neutral state: published without one, it is "all rights reserved," and
nobody may legally deploy it. For a project whose entire stated purpose is
that people running Grok-based agents can stand it up themselves, that
made it unusable by its own audience while looking finished.

**MIT**, copyright `papa-couch` —
[0022](docs/decisions/0022-mit-license.md). Apache-2.0 was the only
serious alternative and was turned down: its patent grant answers a
question this project does not raise, at ten times the text a reader has
to check.

In three places, so the terms travel with the code rather than only
sitting in the repository — `LICENSE` at the root, `license` and the OSI
classifier in `pyproject.toml`, and a plain-language section in the
README. Verified by building a wheel and reading its metadata rather than
trusting the config: `License: MIT`, the classifier, and the `LICENSE`
file bundled inside.

The packaging metadata was thin for the same reason nobody had noticed the
licence, so `readme`, `classifiers` and `[project.urls]` went in alongside.

This also settles an asymmetry. The project takes freely from other
people's permissive work — CJE and GEPA are MIT, and the price table
[0020](docs/decisions/0020-cost-and-limits.md) pins is one MIT-licensed
JSON file from litellm — and until now gave nothing back on the same
terms.

## 2026-09-01 — a guarantee that held only while the colony was young

A review of the finished work, looking for what is missing rather than
what is broken. It found one real bug, closed one open question with
numbers, and turned up three things the repository does not have.

### Fixed

- **Finished work was scored twice on a long-running colony.**
  `score_finished` built its already-scored set from the newest 10,000
  `task_scored` events. Past that many, an older task's score falls out of
  the window, the task reads as unscored, and it is scored again. Verified
  by running it: twenty already-scored tasks, ten thousand newer scores,
  and all twenty were sent to the evaluator a second time. Self-amplifying
  as well — each re-score adds an event that pushes the window further.

  The cost is not only the wasted model call. A duplicate `task_scored`
  event is averaged into the brain's payoff comparison and into the
  calibration sample, so the numbers everything else rests on drift.

  Replaced with `Journal.scored_ids()`, which asks about the tasks in hand
  rather than scanning recent history — one indexed query, bounded by the
  batch, not by how long the colony has been up. The regression test
  reproduces the original conditions and fails without the fix.

### Measured

- **The throughput ceiling** ([question 6](docs/open-questions.md)), which
  had been open since phase 1. ~700 tasks/s end to end with a no-op
  worker, ~600 at 3× the load, ~4,100/s for intake alone. The wall is
  SQLite's commit rate — `record` runs at ~17,700/s on its own and a task
  costs about a dozen committed writes — not the event loop. One of
  [0011](docs/decisions/0011-durable-core.md)'s three thresholds for
  revisiting the durable core now has a number under it, and it is far
  away: a model-backed colony hits its provider's rate limit long before
  it comes near this.

### Made honest

- `Journal.__iter__` delegates to `events(limit=10_000)`, so iterating a
  journal silently stops at ten thousand events. Nothing in the colony
  walks it that way — every consumer asks with its own limit — so the test
  was renamed to pin the cap instead of claiming the whole.

362 tests.

## 2026-08-28 — the parts nothing was watching

No new capability. An audit found three modules carrying the colony's
durability with no tests of their own, and one whole class of failure that
the suite was structurally unable to see. Both are now covered.

### Added

- **`tests/test_journal.py`** (41) — the lease, the dedup claim, the step
  store, recovery from an expired lease, and durability across a restart.
- **`tests/test_slots.py`** (43) — instance reservation, the error classes
  the dispatcher branches on, and the `once` boundary a retry rests on.
- **`tests/test_colony.py`** (29) — the builders' absent-versus-empty
  behaviour, and what happens when a source dies at three in the morning.
- **`tests/test_sdk_contract.py`** (8) — our call shapes bound against the
  installed SDKs' real signatures.
- **`dev` extra** — both backend SDKs, which is what turns the contract
  tests on. Without them that file skips.

### The gap the contract tests close

Every model backend is tested against a stub that accepts anything. That is
the right shape for testing our own logic, and it is also how GEPA's
adapter passed fifteen tests and optimized nothing: 0.1.4 reads an
attribute its documentation calls optional.

Measured rather than assumed: adding one bogus keyword to the Grok
worker's request left all seven of `test_grok.py` green and turned exactly
one test in the whole suite red. Neither SDK's `create` takes `**kwargs`,
so that keyword is a `TypeError` at call time — and three of our five call
sites catch `Exception` broadly and carry on without the model. The router
would stop routing, the judge would stop scoring, and the colony would look
like it was running fine.

The tests bind the keywords our production code actually sends against the
real signature, and hand our code responses built from the SDKs' own
response models. Nothing reaches the network. What this still doesn't prove
is that xAI's server accepts what the SDK accepts — that needs a key,
[question 4](docs/open-questions.md).

### Found while writing them

- **A typo in a `[[source]]` option is swallowed.** `build_source` has a
  guard that names the offending block, and it can never fire: every
  builtin source ends in `**options`. `prot = 9001` instead of `port`
  starts a webhook on the default with nothing said. Pinned by a test that
  documents the behaviour rather than endorsing it, and written up as
  [question 10](docs/open-questions.md) — making the builtins strict is a
  behaviour change to a public surface, so it is a decision, not a patch.
- **Garbage from a worker was not covered by the "not counted as handled"
  test.** Moving the `handled` counter one line earlier passed the suite.
  The scaler reads that number, so a slot returning malformed verdicts
  could have been read as throughput. Closed by a second test.

121 new tests, 361 total. Each of the invariants above was checked by
breaking it: the lease without its status guard fails 3, the step
sentinel 4, a late `busy` flag 4, a sync worker run inline 3, and a
missing price table with a limit set 1. Two mutations that changed
nothing are noted where they apply — cancellation in `source_loop` is
guarded twice over, and only removing both defences turns the test red.

### Also

The audit that prompted this also removed six dead imports and added
`tests/test_console_output.py` — the `init` template's default handler had
no tests, which made it the first thing a new user runs and the last thing
covered.

## 2026-08-27 — a dashboard that can look and cannot act

Phase 6 closes. Decision:
[0021](docs/decisions/0021-a-dashboard-that-cannot-act.md).

### Added

- **`grokklings dashboard`** — one self-contained page on stdlib
  `http.server`: tasks by status, slots and their backlogs, spend against
  the limit, score average, proposals waiting, the unplaced pile, recent
  events. Refreshes every 5s. No dependency.

### What "dashboard" was decided to mean

Three different things were hiding under the word. Someone else's
(Phoenix, Langfuse — fed by `grokklings export`, nothing to build), ours
read-only, and a control panel with buttons. This is the second, and the
third is deliberately not an increment of it.

**The page cannot write, by construction.** The handler defines `do_GET`
and nothing else, so a POST is refused by `BaseHTTPRequestHandler` itself
— verified against a real socket, which answers 501. A page with no
buttons is one commit from a page with buttons; a handler with no write
method has to gain one on purpose. Accepting a proposal from a browser
would move [0016](docs/decisions/0016-brain-proposes-never-applies.md)'s
consent guarantee into a question about who can reach a port, and lifting
a cost limit would undo [0020](docs/decisions/0020-cost-and-limits.md)'s
stop the same way.

It reads the database from outside, like the brain, so it can't slow a tick
or lose a task — and it works on a stopped colony. It binds to loopback,
because the journal holds task payloads.

### Fixed in its own first draft

**The spend bar was drawing a floor as a measurement.** With an unpriced
model in the journal it showed 84% of the limit directly above a warning
that the number was too low. The same lie
[0020](docs/decisions/0020-cost-and-limits.md) refused to tell in the CLI.
It now reads "at least $4.20" and hatches the bar. Found by looking at the
rendered page, not by reading the code.

### Verified

17 new tests, 236 total, most of them against a real socket rather than the
source — "read-only" is a claim about what the process answers. POST, PUT,
PATCH and DELETE all refused; an absent price table reported as absent
rather than as `$0.00`. Rendered in a browser and checked by eye in both
light and dark. Verified the opposite way: adding a `do_POST`, reporting a
missing price table as zero, or treating `0.0.0.0` as loopback each fail
tests.

## 2026-08-27 — cost, and a limit that stops the colony

Decision: [0020](docs/decisions/0020-cost-and-limits.md). The borrowed part
was **data, not code** — a maintained price table — so this is the first
thing taken from outside that costs nothing to install.

### Added

- **`grokklings cost`** — totals the journal's model calls, by model and by
  slot, over all time and over a rolling 24 hours. `--refresh` pins
  litellm's price table (MIT, one JSON) locally.
- **`[cost]` section** — `limit` and `daily_limit`. Crossing one pauses the
  colony: `dispatcher.paused`, the same stop control `Ctrl-C` uses, so
  in-flight work is seen through and nothing is killed.

### The decision this hangs off

**A model with no price is unknown cost, not zero cost.** Treating a
missing rate as `0.0` is the natural implementation and it turns a budget
into decoration: the colony reports spending nothing while spending
steadily, and the limit never fires — precisely the failure a limit exists
to prevent, wearing the appearance of health.

So unpriced calls are counted and their models named, the command exits
non-zero, and **a limit is not enforced against a total known to be
incomplete.** That does mean a runaway unpriced model isn't stopped. It's
the uncomfortable half of the same principle, and it's warned about on
every check rather than swallowed.

Two smaller refusals in the same spirit: prices are **pinned, not fetched
at runtime**, and a limit set with **no pinned price table refuses to
start** — a limit that silently isn't enforced is worse than no limit,
because it was asked for.

### Verified

29 new tests, 219 total. Verified against the real table, not a fake: four
calls of 250k in / 50k out on `grok-4.6` totalled $3.20 and one of 100k /
20k on `claude-opus-5` totalled $1.00, both matching hand arithmetic, and
adding an unpriced model made the command exit 1 saying the figure was low.
Verified the opposite way: treating an unpriced model as free, enforcing
against an incomplete total, or reading a rate-less entry as zero fails
four tests.

### Caught by a test written yesterday

The AST guard added with [0007](docs/decisions/0007-otel-event-names.md)
failed the moment `cost_limit_reached` was recorded — a new event accounted
for in neither the translated nor the ours-alone list. Exactly what it was
for.

### Tidied

Two integration tests from the scaling work were closing their database
while a task was still in flight, printing tracebacks that looked like
product failures and weren't. The suite's output is clean again.

## 2026-08-27 — the journal speaks OpenTelemetry, without a rename

[0007](docs/decisions/0007-otel-event-names.md), open since the first
survey, accepted — but the rename it proposed didn't happen.

### Added

- **`grokklings/otel.py`** — the convention's operation and attribute names
  in one place, and a translation from our events to theirs.
- **`grokklings export`** — the translatable events as OTLP-shaped JSON
  lines, and a line on stderr saying how many events *weren't* translated,
  because that's the design and not a failure.

### Translated, not renamed

0007 said "name journal events on this schema." Doing that literally would
have been wrong, for a reason the record had half-identified itself:

- Most of our events have no equivalent. `task_unplaced`, `task_looped`,
  `step_replayed`, `proposal_made`, `scaling_decision` mean something this
  design means and the convention doesn't. Renaming them would claim a
  meaning that isn't there.
- A rename breaks every journal on disk. Translation touches no stored data.
- **The convention is at Development status** — read off the spec, not
  assumed. When names move, one file moves and no data does.
- Adopting no dashboard costs nothing: nothing in the colony imports the
  module.

### The scope, remeasured

0007 recorded 28 call sites in 8 files, 24 names, "roughly a third" mapping.
Counted again by walking the AST: **38 call sites, 12 files, 33 names** —
and **five** translate, not eleven. Everything the project has grown since
(routing, scoring, proposals, calibration, optimization, scaling) is its own
vocabulary. The other 28 are listed by name, and a test walks the AST to
fail if a new event is accounted for in neither list.

### Checked, not guessed

- **xAI's registered provider name is `x_ai`, not `xai`** — from the
  attribute registry. The obvious guess is wrong and would produce spans
  that look conformant.
- **An unknown model gets no provider rather than a default.**
  `gen_ai.provider.name` is required with a registry of allowed values;
  inventing one is worse than omitting it.

### Verified

17 new tests, 190 total, plus an end-to-end export of a hand-built journal:
4 of 8 events came out, carrying `x_ai` and `gen_ai.usage.*`. Verified the
opposite way too: guessing a provider, turning every note into a chat span,
or forcing a mapping for an event with no home fails six tests.

**Not verified:** whether a dashboard actually ingests the file. 0007 set
that as its own test — stand up Phoenix or Langfuse and see a task's
history without glue — and nobody has run it.

## 2026-08-27 — a third survey, for phases 4 and 6

[docs/research/2026-08-before-phases-4-and-6.md](docs/research/2026-08-before-phases-4-and-6.md).
Nothing adopted, nothing decided.

### Phase 4: the check 0009 deferred

All three memory candidates opened. **Every one needs infrastructure the
colony doesn't have**, so the question stops being "which library" and
becomes "will the colony require a database" — the same question
[0011](docs/decisions/0011-durable-core.md) asks.

- **Graphiti** ✅ (Apache-2.0) states our conflict rule almost verbatim:
  old facts are invalidated, not deleted, with validity windows. Needs a
  graph database; its embedded option (`falkordblite`) removes the server
  but **requires Python 3.12+**, raising this project's floor from 3.11.
- **mem0** ✅ (Apache-2.0) self-hosts as `docker compose up`, and its
  defaults reach for OpenAI embeddings — a second provider in a Grok-first
  colony.
- ⚠️ Zep's Community Edition is *reported* deprecated by a comparison
  article, **not confirmed** against Zep's own repository. Not repeated as
  fact.

0009 updated with all of this, still **proposed**, plus a cheaper option it
never weighed: the journal already stores versioned contributions with an
author and a timestamp.

### Phase 6: cost tracking is the cheap part

**litellm's price table** (MIT, one JSON, no dependency on litellm itself)
is the maintained rates we'd otherwise have to keep current by hand.
Verified by fetching it: 3365 models, 36 under the `xai` provider,
including our default — `xai/grok-4.6` at $2e-06 in / $6e-06 out. Token
usage is already journalled on every model call, so cost is a lookup and a
multiply. What's ours is deciding what happens when a limit is hit.

**Arize Phoenix is Elastic License 2.0** ✅, not open source in the usual
sense — comparison articles say otherwise. Everything else this project
borrows is MIT or Apache-2.0. Langfuse's v3 is four services, which is the
wrong shape for a colony whose setup is `git clone`.

### Raised 0007's priority

Every candidate dashboard ingests OpenTelemetry. If the journal spoke that
vocabulary, adopting one would be an exporter and adopting none would cost
nothing — a better argument for
[0007](docs/decisions/0007-otel-event-names.md) than the one it makes for
itself. Noted in the record; scope unchanged.

## 2026-08-27 — phase 3: clones that don't churn

`add_instance` and `remove_instance` had been sitting there since phase 1
with a comment calling them the extension point. This is what calls them.
Decision: [0019](docs/decisions/0019-clones-scale-on-a-hold-time.md).

### Added

- **`grokklings/scaling.py`** — a watcher at the top of each dispatcher
  tick: spawns a clone under sustained pressure, gives one back after
  sustained idleness, one step at a time. Journals every decision with the
  backlog behind it.
- **`[scaling]` section** — `min_instances`, `queue_per_instance`,
  `grow_after`, `shrink_after`. With it, `max_instances` becomes a ceiling
  and the slot starts at the floor. Without it, nothing changes: a slot
  gets `max_instances` at startup and keeps them.

### The hold time is the whole design

Queue depth is spiky. A rule that reacts to the current value spawns on a
burst, collapses on the lull a second later, and churns instances instead
of draining work. So pressure has to persist before anything happens, and
the clock resets the moment it lets up. Growing waits 10 seconds, shrinking
60 — being slow to grow costs latency, being slow to shrink costs nothing,
and thresholds should reflect that the two mistakes aren't equal.

Unrouted work counts toward a single entry slot. Freshly submitted tasks
aren't routed yet, so without that the slot actually taking the load would
never grow. With several entry slots the router decides later, so those go
unattributed rather than being guessed at.

### Fixed a trap that was already live

**`[scaling]` with no keys under it parses to an empty dict**, and the
first version read that as "no section" — so a user who wrote the header
expecting the defaults got nothing, silently. The same trap was already
shipped in `[routing]` from [0014](docs/decisions/0014-grok-router.md).
Both now tell an absent section (`None`) from an empty one (`{}`).

### Verified

28 new tests, 173 total. Most of them are about *not* scaling — a burst
that clears, a covered backlog, the ceiling, the floor, a slot still
working. Two drive a real colony rather than fakes: 30 tasks into a
one-instance entry slot grew it to its ceiling of 4, one at a time, and the
same colony without `[scaling]` never spawned. Verified the opposite way
too: removing the hold time, the ceiling, or the entry attribution fails
five tests.

## 2026-08-27 — GEPA optimizes a slot's instructions

Phase 5 closes. [0010](docs/decisions/0010-brain-scope.md) proposed handing
context editing to GEPA back when nothing existed to give it; all three of
its requirements are met now, so it's adopted —
[0018](docs/decisions/0018-gepa-optimizes-context.md).

### Added

- **`grokklings optimize --slot triage --budget 60`** — GEPA searches for
  better instructions: propose, run, score against your criteria, read why,
  propose again, keep what survives.
- **`grokklings/optimize.py`** — a `SlotAdapter` implementing GEPA's two
  required methods, and a bridge from its synchronous engine to our async
  workers and judge. `optimize` extra installs it.

### The result is still a proposal

The optimized context is recorded as an `edit_context` proposal and goes
through the same consent gate as everything the brain suggests. A better
context that installed itself would still be the colony rewriting its own
instructions, and the rewrite being measured doesn't change who decides.

### Found by running the real engine

**A method GEPA documents as optional isn't.** The `GEPAAdapter` docstring
says `propose_new_texts` is optional and that GEPA provides a default; the
0.1.4 engine reads `self.adapter.propose_new_texts` directly instead of
through `getattr`. An adapter that omits it raises `AttributeError`
mid-search, reflection silently stops proposing, and the "optimized"
context comes back **unchanged with no error reaching the caller**. The
first version of the adapter passed every test written against the
documented interface while optimizing nothing.

**A crash and an unjudgeable run were being reported the same way.** Both
produce no assessment, and they teach the teacher model opposite lessons:
one says the instructions produce something broken, the other says nothing
about them at all. Caught by a test that expected the second message and
got the first.

### Verified

15 new tests, 145 total, and the important one drives GEPA itself rather
than a fake: a planted colony where the wanted instruction was "cite the
source" went from 0.20 to 1.00 with the teacher consulted once.

### A warning that belongs with this, not after it

GEPA optimizes toward whatever the judge rewards. Run against an
uncalibrated judge ([0017](docs/decisions/0017-calibrate-the-judge.md)) it
will faithfully drive the instructions toward that judge's bias, and the
result will score beautifully. Nothing in the code enforces the ordering.

## 2026-08-27 — checking the judge

Adopted `cimo-labs/cje` rather than writing a comparison ourselves.
Decision: [0017](docs/decisions/0017-calibrate-the-judge.md).

### Added

- **`grokklings calibrate`** — `--export` writes a random sample to judge by
  hand, `--labels` reads it back and reports the judge's own average, the
  calibrated estimate, a 95% CI, and the drift between them.
- **`grokklings/calibrate.py`** wrapping CJE; `calibrate` extra installs it.

### Three details that would have quietly invalidated the check

- **The worksheet hides the judge's score.** Shown the number you're
  checking, you agree with it, and what gets measured is your anchoring.
- **The sample is random, not the cases that look wrong** — CJE needs a
  random slice, and labeling only your suspicions calibrates against a
  distribution that doesn't occur.
- **Ten labels is a floor, not a suggestion.** Below it `calibrate` raises
  rather than returning an interval that will be quoted anyway.

### Fixed while building

**Every honest run was reporting a failure.** The first version treated any
note in CJE's boundary card as a warning and exited non-zero — but CJE
grades coverage in two levels, and its `CAUTION` explicitly says no
material extrapolation was detected. Only `REFUSE-LEVEL` means don't trust
the number. Collapsing them would have made the warning worthless by the
second run. Also quieted CJE's INFO narration, which was printing fifteen
lines of fitting internals ahead of the answer.

### Verified

11 new tests, 130 total, and this one was verified against the real
library rather than a fake client: installed CJE 0.7.0 and planted a judge
scoring 0.2 high over 100 tasks — the calibration recovered the true mean
and reported the drift as +0.18. End to end through the CLI on a seeded
database too. Verified the opposite way: leaking the judge's score into the
worksheet, dropping the label floor, and sampling the worst cases instead
of at random each fail tests — five in total.

**Not done, and it can't be done here:** the actual calibration of *this*
project's judge. That needs someone to label thirty real tasks by hand.
Open question 8 stays open, now as a task rather than a design problem.

## 2026-08-27 — the brain proposes

Phase 5, the half of it that doesn't need an optimizer. Decision:
[0016](docs/decisions/0016-brain-proposes-never-applies.md).

### Added

- **`grokklings/brain.py`** — reads the unplaced pile, the failures, and the
  scores, and returns proposals: `new_role`, `split_slot`, `edit_context`,
  `retire_slot`. Every proposal carries a rationale, the task ids it rests
  on, and the concrete TOML.
- **`grokklings brain`** to propose, **`grokklings proposals`** to review,
  accept (`--accept ID`) or reject (`--reject ID`).
- **`payoff()`** — a slot's scores before vs. after an accepted change, and
  "not enough yet" below three samples either side.

### Borrowed rather than invented

- **Consent as a required argument**, from `BerriAI/self-improving-agent`:
  `apply(journal, proposal, confirmed)` raises when `confirmed` is false,
  the same way their tool schema requires `userConfirmedInThisMessage` and
  the executor throws on false. A caller who forgets gets an exception, not
  a silent change.
- **The bar a proposal must clear**, from the OpenAI Agents SDK
  orchestration guidance: capability isolation, policy isolation, prompt
  clarity, trace legibility — plus its bias against growth. A proposal
  clearing none of them is dropped before the user sees it.

### What the brain deliberately cannot do

Nothing in the colony reads proposals. The brain never edits the config —
accepting prints the TOML to paste. A colony that never runs `proposals`
gets no benefit from the brain, and that is the intent, not an oversight.

### Verified

19 new tests, 119 total. Verified the opposite way too: making consent
advisory, letting ungrounded proposals through, and allowing a payoff
verdict from any number of samples each fail tests — four in total.

### Caught before it shipped

The journal's event id column is `seq`, not `id`. The first draft read
`event["id"]`, which would have given every proposal an empty id — so no
acceptance would ever have matched its proposal, and `pending()` would have
listed accepted proposals forever.

## 2026-08-27 — a second survey, before phase 5

[docs/research/2026-08-before-phase-5.md](docs/research/2026-08-before-phase-5.md).
Nothing adopted, nothing run — a survey, and it corrects the first one in
three places.

### Corrected

- **The "three to seven agents" ceiling doesn't exist.** The first survey
  carried it as ⚠️ with "verify before relying on it in phase 5." Checked
  both primary sources; neither states any limit. Closes open question 2,
  and the brain gets no hard cap on that authority. What the source does
  give is a better bar anyway: add a specialist only when it materially
  improves capability isolation, policy isolation, prompt clarity, or trace
  legibility.
- **GEPA's evidence is no longer only its own README** — the paper is an
  ICLR 2026 Oral. ⚠️ Peer review isn't replication. Combined with
  [0015](docs/decisions/0015-success-is-the-users-answer.md) supplying the
  quality metric it needs, GEPA went from blocked to adoptable;
  [0010](docs/decisions/0010-brain-scope.md) updated accordingly, still
  **proposed**.
- **"What nobody else has" was overstated.** `EvoAgentX` (natural-language
  workflow generation, a self-evolving engine, optional human-in-the-loop)
  and `BerriAI/self-improving-agent` (propose a diff, human approves, draft
  PR) overlap two of the three claims. About one and a half survive. The
  original text is left standing with a pointer to the correction.

### Found

- **`cimo-labs/cje`** (MIT) — calibrates judge scores against a slice of
  ground-truth labels, with confidence intervals and a flag when scores
  extrapolate. It's the method open question 8 already describes, done with
  statistics.
- **Self-preference bias applies to us directly.** Our evaluator judges
  Grok's output with Grok. It's a named, tooled-for phenomenon; ⚠️ its size
  in the judging-yourself case isn't established. The mitigation is cheap
  if needed — `[success]` already takes its own `model` key.

## 2026-08-27 — what counts as success

The question that blocked phase 5 from the start, answered — by the answer
being that there isn't one answer.

### Added

- **`[success]` section** — the user's own criteria, in their own words:
  `looking_for`, `counts_as`, `never`. A slot may add its own `success`
  for what a good result from that slot looks like; the worker never sees
  it. Decision: [0015](docs/decisions/0015-success-is-the-users-answer.md).
- **`grokklings/evaluate.py`** — `GrokEvaluator` scores a finished task
  against those criteria and returns a score, whether it met the bar,
  actionable feedback, and any red lines crossed.
- **`python -m grokklings score`** — scores finished tasks on demand, never
  inside the pipeline. Writes `task_scored` journal events, which is what
  the brain will read. Exits non-zero when a red line was crossed.

### Three choices worth naming

- **A crossed red line is not a low score.** Work can be excellent at what
  it was asked to do and still do something forbidden; folding the two
  together would average away the case worth catching.
- **"Couldn't tell" is not "was bad."** A failed scoring call records
  nothing and returns None rather than a zero — a zero would poison every
  average built on it, and the task would look judged when it wasn't.
  Unscored tasks are picked up next pass.
- **Scoring is on demand, outside the pipeline.** A task's fate never
  depends on whether anyone got around to judging it.

### Verified

17 new tests, 100 total. Verified the opposite way too: recording a failed
call as a zero, and letting a violation collapse into the score, each fail
a test.

Not verified: whether the evaluator judges the way a given user meant —
open question 8, and the stakes are higher than for the router, since a
miscalibrated judge produces confident numbers rather than visible
mistakes.

### Closed

Open question 3, "what counts as success," open since the project's first
day and blocking phase 5 per
[0010](docs/decisions/0010-brain-scope.md). Phase 5 is no longer blocked.

## 2026-08-27 — phase 2 finished: picking a slot

### Added

- **`grokklings/routing.py`** — a router behind an `async pick(task,
  candidates)` seam, with `GrokRouter` asking Grok to choose by the slots'
  descriptions. Configured by a `[routing]` section; without one, nothing
  changes. Decision: [0014](docs/decisions/0014-grok-router.md).
- **Several slots may be marked `entry = true`** when a router is
  configured — previously a config error. The router picks between them.
- **A `not_mine` verdict is rehomed** to a slot the task hasn't visited
  instead of dead-ending on the unplaced pile. Only a task nobody will take
  lands there now. New journal events: `slot_picked`, `task_rehomed`,
  `slot_pick_failed`.

### Changed

- `Dispatcher.route()` and `_apply()` are coroutines now — both were
  already called from async code.
- [0008](docs/decisions/0008-semantic-routing.md) is **superseded** by 0014:
  phase 2's picking was built, but by asking Grok rather than by pulling in
  a local encoder. The reasoning is in 0014 — 0008 predates Grok becoming
  the primary backend, which flips its cost trade.

### Found while building

**A router that throws would have stalled the task, not lost it — but
stalled it repeatedly.** `GrokRouter` catches its own failures, but any
user-supplied router that raised would leave the task in progress until its
lease expired, then do it again. The dispatcher now catches at the seam and
falls back to no-router behavior. Caught by a test written on the wrong
assumption: it expected the exception to surface, and finding out it didn't
is what exposed the stall.

### Verified

16 new tests, 83 total. Verified the opposite way too: disabling the
picking makes four of them fail. Not verified: whether the router picks
*well* — that needs a key and real traffic, and is now open question 7.

## 2026-08-27 — Grok as the primary backend

### Added

- **`grokklings/handlers/grok.py`** — a model-backed worker for xAI's Grok,
  via the OpenAI-compatible `openai` client. Same verdict contract as the
  Anthropic worker. Decision: [0013](docs/decisions/0013-grok-primary-backend.md).
- **`grokklings/handlers/_contract.py`** — the prompt/schema contract shared
  by both model-backed workers, pulled out so it isn't duplicated.

### Changed

- **`grokklings/handlers/llm.py` renamed to `grokklings/handlers/anthropic.py`**
  — now one of two backends, not the only one. Behavior unchanged.
- The README's quick start, the "writing your own worker" section, and
  `examples/watcher.toml` now use `grokklings.handlers.grok:handle` as the
  default; Anthropic is documented right below as the alternative.
- `pyproject.toml`'s `llm` extra split into `grok` (installs `openai`) and
  `anthropic` (installs `anthropic`).
- The `init` template now points at the Grok handler in a comment. It still
  generates an echo-handler config, so a first `run` needs no key and no
  install.
- [0001](docs/decisions/0001-python-stdlib.md) carries a note that its
  `grokklings[llm]` extra no longer exists; the decision itself stands.

### Verified

7 new tests for the Grok worker on a fake client, 67 total — request shape,
slot config reaching the request, verdict parsing, the usage note, and the
empty-response error. Verified the opposite way too: breaking the schema's
`strict` flag and the system/user message order fails two of them. The real
call against xAI is still unverified — no key in this environment; open
question 4 now says exactly what that leaves untested.

## 2026-08-27 — the whole repository, in English

### Changed

- **Every file in the repository translated to English**: code, comments,
  docstrings, error messages, CLI help text, test data, example configs, and
  every document. Nothing left untranslated except this file's own history
  below, which is a record of what happened and stays as it was written.
- **Error messages and their tests were translated together**, since 23
  assertions checked Russian substrings verbatim (`assertRaisesRegex`,
  `assertIn`) — a message translated without its test would have silently
  broken the suite. All 60 tests pass after the pass, run five times in a
  row for stability.
- Example slot and output names now read in English throughout
  (`triage`, `deep_dive`, `archive`, `notify`, and so on), consistently
  between `examples/*.toml`, `README.md`, and the test suite.

### Corrected while translating

- **The "half a day" estimate for [0007](docs/decisions/0007-otel-event-names.md)
  wasn't grounded in anything** and was already retracted in conversation
  before this pass — the decision record now carries the actual measured
  scope (28 call sites across 8 files, 24 distinct event names, roughly a
  third mapping onto the convention) instead of a made-up number.
- [0002](docs/decisions/0002-queue-in-database.md) and
  [0011](docs/decisions/0011-durable-core.md) referenced step-level
  idempotency as a known gap — both now point at
  [0012](docs/decisions/0012-step-idempotency.md), which closed it.

## 2026-08-27 — a non-repeatable step

### Added

- **`ctx.once(key, fn)`** — a piece of work runs at most once per task's
  lifetime; a retry gets back the recorded result. For coroutines,
  `ctx.once_async`. Decision: [0012](docs/decisions/0012-step-idempotency.md).
- **`ctx.idempotency_key(name)`** — a stable key for an external service,
  the same across every attempt of one step.
- A `steps` table in the database; `step_completed` and `step_replayed`
  journal events.

### Fixed

- **The file output was appending a second identical line**, and the
  webhook output was sending a second notification, whenever a crash
  happened right after a successful delivery. Both cases are now reproduced
  by tests. The webhook now sends an `Idempotency-Key`.

### Verified

12 new tests, 60 total. Verified the opposite way too: with the mechanism
disabled, five of the twelve fail, including both output tests.

---

## 2026-08-27 — phase 1 and the document structure

### Added

- **Phase 1, in full.** Intake, dispatcher, slots, outputs, the task, the
  journal, deduplication, failure handling. 48 tests, an end-to-end run:
  webhook -> intake -> slot -> file.
- **A document structure.** A map ([docs/README.md](docs/README.md)),
  decision records, a phase plan, open questions, an ecosystem survey.
- **Decision records 0001-0006** — what's already accepted and in the code.
- **Records 0007-0011** — proposals from the ecosystem survey, marked
  "proposed" and "deferred." None of them are in the code.

### Fixed during development

Three bugs found by tests before the first commit:

- a single dispatcher pass could hand one instance several tasks — the
  "busy" flag was set inside the coroutine instead of at assignment time;
- "done" from the first output cut off delivery to the rest;
- an instance was lost forever if its job got cancelled before the
  coroutine started.

The last one is closed by record
[0006](docs/decisions/0006-instance-reservation.md) and two tests.

### Corrected

- **GEPA doesn't run on a journal alone.** The survey's first draft said our
  journal already is the execution traces GEPA needs, so half the brain was
  basically built. Checking the primary source showed it also needs a
  quality metric, a training set, and an evaluation function with feedback.
  The correction is in [0010](docs/decisions/0010-brain-scope.md) and the
  survey itself.
- **The agentic part of the OpenTelemetry convention isn't stable** — it's
  at Development status. Noted in
  [0007](docs/decisions/0007-otel-event-names.md).

### Moved

The project was split out of the `papa-couch/agent` repository into its
own. The `claude/grokklings-architecture-pipeline-avcnp9` branch in the old
repository holds the same work and is slated for deletion.

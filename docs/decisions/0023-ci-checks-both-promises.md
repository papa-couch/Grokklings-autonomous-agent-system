# 0023. CI checks both promises, and fails on silence

- Status: **accepted**
- Date: 2026-09-01
- Related: [0001](0001-python-stdlib.md), [0013](0013-grok-primary-backend.md), [0022](0022-mit-license.md)

## Context

362 tests and nothing ran them but whoever happened to be at a keyboard.
A contributor's pull request had no automatic check at all, and
[0022](0022-mit-license.md) had just made outside contributions possible
for the first time.

The obvious workflow — install everything, run the suite — would have been
worse than useless here, because this project makes two promises and that
job only checks the weaker one.

The first promise is [0001](0001-python-stdlib.md): the core runs on
nothing but the standard library. Nobody had ever actually verified it the
way a new contributor experiences it, from a clean checkout with no extras.
Trying it turned up two failures. `optimize_context` looked for GEPA before
it validated its arguments, so the two tests covering its guards could not
run without the optional dependency they were meant to be independent of.

The second promise is the optional half — both model backends, CJE, GEPA.
There the dangerous failure is not red, it is silence. Every one of those
tests skips itself when its dependency is missing. A broken extras install
therefore leaves the suite green with 24 tests quietly not run, including
the SDK contract tests that are the only thing standing between us and the
class of bug that let GEPA's adapter pass fifteen tests while optimizing
nothing.

## Decision

Three jobs in `.github/workflows/tests.yml`, on every push and pull request.

**`stdlib-only`**, across Python 3.11, 3.12 and 3.13. Installs the package
with no extras and runs the suite. Before running it, it imports every
module in the package and asserts that none of `openai`, `anthropic`,
`cje`, `gepa` or `numpy` ended up in `sys.modules` — which catches a lazy
import that has quietly moved to the top of a file, the way a stdlib-only
core stops being one. `__main__` is excluded from that walk, because
importing it runs the CLI.

**`with-extras`**, on 3.11 only. Installs `dev`, `calibrate` and
`optimize`, and **fails if any test skips at all.** That inversion is the
point of the job: here a skip does not mean "not applicable", it means a
dependency did not install and the test never ran. Green and unchecked is
worse than red.

It is pinned to the floor version deliberately. The job exists to check our
code against real third-party packages, not to survey which Python versions
those packages support.

**`lint`** — pyflakes over the source and tests, plus the documentation
link check the audits had been running by hand.

### What had to be fixed to make this pass

`optimize_context` now validates its arguments before it looks for GEPA.
Beyond making the guards testable, it is the better error: telling someone
to install a package and letting them discover afterwards that their call
was wrong anyway wastes their time twice.

## Consequences

- A pull request is checkable without anyone reading it first.
- [0001](0001-python-stdlib.md) stops being a claim and becomes a check
  that runs on every push. So does the Python version range advertised in
  `pyproject.toml`'s classifiers.
- Someone who breaks an extra finds out, instead of getting a green tick
  over 24 tests that never ran.
- CI needs no secrets. Nothing here calls a model, and nothing should: an
  API key in CI would make every push cost money and every fork a risk.
  This is also the ceiling of what CI can prove — the real-API question
  ([question 4](../open-questions.md)) stays open by design.
- Three jobs cost more minutes than one. On a repository this size that is
  free, and the alternative is a check that passes for the wrong reason.

## How to undo it

Delete `.github/workflows/tests.yml`; nothing else depends on it. The
argument-ordering fix in `optimize_context` should stay either way — it
stands on its own, and reverting it would break the two guard tests on any
machine without GEPA.

To keep CI but drop the strictness: remove the skip check from the
`with-extras` job to go back to an ordinary run, or drop 3.12 and 3.13
from the matrix. Both weaken exactly the thing the file was written for,
so the reason should be written down here first.

## Note, 2026-09-01

Both actions were bumped to `@v7` the same day. GitHub had deprecated
Node 20 and was forcing `checkout` and `setup-python` onto Node 24 with a
warning on every job; v7 of each declares `runs.using: node24`. Checked
before bumping three majors at once: `python-version` is still an input of
`setup-python`, and `checkout` is called with no inputs here.

## Sources

- Verified locally before committing, in both directions. On a clean
  virtual environment with no extras the suite passes on 3.11, 3.12 and
  3.13 with 24 skips; with every extra it passes with none. The lint
  pipeline passes on a clean tree and fails on an introduced dead import.
  The leak check fails when a third-party import is moved to module level.
  The first draft of the lint step had the opposite bug — `grep -v` exits
  non-zero when it finds nothing, so the step failed precisely when the
  tree was clean.

# 0022. MIT, so the repository can actually be used

- Status: **accepted**
- Date: 2026-09-01
- Related: [0013](0013-grok-primary-backend.md), [0020](0020-cost-and-limits.md)

## Context

The project had no licence file at all. That is not a neutral state: a
repository published without one is "all rights reserved" by default, so
nobody may legally copy, modify or deploy it. Anyone reading carefully —
in particular anyone at a company, where this gets checked — has to walk
away, and the ones who don't check are quietly in the wrong.

That is the exact opposite of what this repository is for. The stated
purpose, from the start, is that people running Grok-based agents can pick
it up and stand the system up themselves
([0013](0013-grok-primary-backend.md)). A missing licence made the whole
thing unusable for its own audience while looking finished.

It was also asymmetric. The project takes freely from other people's
permissive work — CJE and GEPA are MIT, and the price table
[0020](0020-cost-and-limits.md) pins is one MIT-licensed JSON file from
litellm — and gave nothing back on the same terms.

## Decision

**MIT.** Copyright holder: `papa-couch`.

Three places, so the licence travels with the code rather than only
sitting in the repository:

- `LICENSE` at the root — the standard text, which is what GitHub reads to
  put an "MIT" badge beside the repository name;
- `pyproject.toml` — `license = {text = "MIT"}` plus the OSI classifier,
  verified by building a wheel and reading its metadata: `License: MIT`,
  `Classifier: License :: OSI Approved :: MIT License`, and the `LICENSE`
  file bundled inside the wheel;
- `README.md` — a short section saying what it means in plain terms, and
  naming the licences of the optional extras.

### Why MIT and not Apache-2.0

Apache-2.0 was the only serious alternative. It grants the same
permissions and adds an explicit patent grant plus a requirement to mark
changes — which is why legal departments often prefer it.

MIT was chosen because the patent clause answers a question this project
does not raise, and the cost is real: Apache-2.0 is roughly ten times the
text, and it is text a reader has to actually check. MIT is short enough
that people recognise it without reading, which lowers the barrier to
picking the project up. For a project whose entire point is being picked
up, that is the trade worth making.

Nothing about this is hard to change later: MIT is compatible with
relicensing to Apache-2.0 while there is a single copyright holder.

### Why the copyright year is 2026 and not a range

One year, the year of first publication. A range gets stale and has to be
maintained; git already holds the real history.

## Consequences

- Anyone may use, modify, redistribute and sell this, including inside
  closed commercial software, on two conditions: the copyright line stays,
  and there is no warranty.
- The project stops being a taker-only participant in the permissive
  ecosystem it draws on.
- Contributions from other people become possible in a normal way. Without
  a licence there was no basis on which to accept one.
- The `LICENSE` file now ships inside the wheel, so a user who only ever
  installs the package still has the terms.
- **What this does not do:** it says nothing about the trademark or the
  name, and it is not a claim about anyone else's rights. The independence
  note next to it — that this is not affiliated with or endorsed by xAI —
  is a separate statement of fact, not a licence term.

## How to undo it

Deleting the file does not undo it: anyone who received a copy under MIT
keeps those rights to that copy, permanently. That is the point of a
licence and it cannot be revoked retroactively.

What *can* be done is relicensing future versions. While `papa-couch` is
the sole copyright holder that is a matter of replacing `LICENSE`, the
`pyproject.toml` fields and the README section, and saying so in the
changelog. Once other people have contributed code, every one of them has
to agree — which is the usual reason projects find they cannot change
licence later. If a different licence is ever wanted, the cheap moment to
switch is now, before there are contributors.

## Sources

- The MIT licence text, as published by the Open Source Initiative.
- Verified locally rather than assumed: a built wheel carries
  `License: MIT`, the OSI classifier, and the `LICENSE` file itself.

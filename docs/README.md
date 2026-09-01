# Project documents

A map: what lives where, and in what order to read it.

## Where to start

| Document | About | When to open it |
|---|---|---|
| [../README.md](../README.md) | what this is, how to run it, how to write your own worker | first |
| [architecture.md](architecture.md) | what's actually built, what isn't, where to build next | when you sit down to write code |
| [plan.md](plan.md) | phases 1-6: each one's status and what's next | when you're deciding what to pick up |
| [glossary.md](glossary.md) | architecture terms <-> names in code | when you're reading code and lose the thread |
| [publishing.md](publishing.md) | the settings that no commit can set: description, topics, visibility | when taking it public |

## Where decisions are recorded

| Document | About |
|---|---|
| [decisions/](decisions/) | why things are built the way they are, and how to undo it |
| [open-questions.md](open-questions.md) | what's still unresolved, and how to settle it |
| [research/](research/) | what we found in other people's projects, with links and a verification mark — [the first survey](research/2026-08-ecosystem.md), then [a second one before phase 5](research/2026-08-before-phase-5.md) that corrects parts of it, and [a third for phases 4 and 6](research/2026-08-before-phases-4-and-6.md) |
| [../CHANGELOG.md](../CHANGELOG.md) | what changed in the project and when |

## Rules that keep this from turning into a mess

1. **A decision lives in `decisions/`, not in chat history or a code comment.** A comment explains a line; a decision record explains a choice.
2. **Every record is reversible.** It has a "how to undo it" section — if a decision turns out wrong, the rollback doesn't have to be invented from scratch.
3. **A proposal and a decision are different things.** Every record has a status. As long as it says "proposed," it isn't in the code and shouldn't be.
4. **A borrowed claim is marked with its source.** In `research/`, every finding says whether it was checked against the primary source or taken from a survey article. An unverified claim never becomes the basis for a decision.

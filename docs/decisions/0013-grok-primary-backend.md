# 0013. Grok is the primary model-backed worker

- Status: **accepted**
- Date: 2026-08-27
- Related: [0001](0001-python-stdlib.md)

## Context

The colony's model-backed worker (`grokklings/handlers/llm.py`) has so far
been Anthropic-only. Separately, the user wants the project positioned for
people running Grok-based agents: when someone lands on this repository,
the README and the default example should make it obvious that Grokklings
is a colony for Grok, not "a Claude thing with Grok bolted on."

xAI's Grok API is OpenAI-compatible (`base_url = https://api.x.ai/v1`,
`/chat/completions`, `response_format={"type": "json_schema", ...}` with
`strict: true`) — verified against the primary source
(docs.x.ai/docs/guides/structured-outputs, fetched 2026-08-27). That means
the same verdict schema and prompt contract the Anthropic worker already
uses could be reused almost as-is, through the `openai` Python client
instead of `anthropic`.

This is a positioning decision, not a claim of any kind about affiliation
with xAI: Grokklings is an independent project built for convenience —
"deploy a colony of Grok-backed agents easily" — nothing here implies
partnership, endorsement, or that this repo is part of xAI's own agentic
system. That line matters and isn't up for revisiting under this record.

## Decision

- The shared prompt contract (`VERDICT_SCHEMA`, `CONTRACT`, `build_system`,
  `build_message`) moves out of the Anthropic-specific file into
  `grokklings/handlers/_contract.py`, so it isn't duplicated between backends.
- `grokklings/handlers/grok.py` is added: same contract, talks to xAI via the
  `openai` package's `AsyncOpenAI(base_url="https://api.x.ai/v1")`, reads
  `XAI_API_KEY`, defaults to `model = "grok-4.6"`.
- `grokklings/handlers/llm.py` is renamed to `grokklings/handlers/anthropic.py`
  — now one of two backends, not "the" LLM worker. Its behavior is
  unchanged.
- `grokklings.grok:handle` becomes the handler used in the README's quick
  start, the "writing your own worker" section, and `examples/watcher.toml`.
  Anthropic stays fully supported, documented right below it as the
  alternative with the same config shape.
- `pyproject.toml` gains two extras, `grok` (installs `openai`) and
  `anthropic` (installs `anthropic`), replacing the single `llm` extra.

## Consequences

- Anyone with an existing config pointing at `grokklings.handlers.llm:handle`
  needs to change it to `grokklings.handlers.anthropic:handle` — there's no
  installed base yet, so no deprecation shim was worth adding.
- Two backends to maintain instead of one, though the shared contract module
  keeps the actual duplication small (each backend file is just its client
  setup and the API call).
- Neither backend has run against a real API in this environment (no keys
  for either) — tracked as open question 4, updated to cover both.

## How to undo it

Revert this commit. Concretely: move `_contract.py`'s contents back into
`anthropic.py` (or wherever the sole LLM worker should live), delete
`grok.py`, drop the `grok` extra, and point the README and
`examples/watcher.toml` back at whichever backend should be primary. Nothing
downstream depends on `grok.py` existing — the dispatcher only ever sees a
`(task, ctx) -> Verdict` callable named in a slot's `handler` config.

## Sources

- xAI, "Structured Outputs," docs.x.ai/docs/guides/structured-outputs —
  fetched and read directly, 2026-08-27: confirms the OpenAI-compatible
  base URL, auth via `XAI_API_KEY`, and the `json_schema` response format
  with `strict: true`.

"""A worker that thinks with Claude, via the Anthropic API.

The same contract as `grok.py`, for a slot that should think with Claude
instead: the slot's description and context become the system prompt, the
task's payload and accumulated result become the message, and the verdict
comes back as structured output, so the contract is checked by a schema
instead of parsed out of text.

Requires `pip install grokklings[anthropic]` (the anthropic package) and a
key in the environment (ANTHROPIC_API_KEY, or an `ant auth login` profile).
"""

from __future__ import annotations

import json
from typing import Any

from ..model import Task, Verdict
from ..slots import WorkerContext, WorkerCrashed
from ._contract import VERDICT_SCHEMA, build_message, build_system

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_MAX_TOKENS = 16000

_client = None


def _get_client() -> Any:
    global _client
    if _client is None:
        try:
            import anthropic
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on the environment
            raise WorkerCrashed(
                "the Anthropic worker needs the anthropic package: pip install 'grokklings[anthropic]'"
            ) from exc
        _client = anthropic.AsyncAnthropic()
    return _client


async def handle(task: Task, ctx: WorkerContext) -> Verdict:
    client = _get_client()
    options = ctx.options
    model = options.get("model", DEFAULT_MODEL)
    # Give the client a bit less time than the slot: let the SDK raise its
    # own error instead of the slot's timeout cutting it off.
    request_timeout = max(5.0, ctx.slot.timeout - 5.0)

    response = await client.with_options(timeout=request_timeout).beta.messages.create(
        model=model,
        max_tokens=options.get("max_tokens", DEFAULT_MAX_TOKENS),
        system=build_system(ctx),
        thinking={"type": "adaptive"},
        output_config={
            "effort": options.get("effort", "high"),
            "format": {"type": "json_schema", "schema": VERDICT_SCHEMA},
        },
        # A policy refusal doesn't sink the task: the same request finishes
        # out on the fallback model inside the same call.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": build_message(task)}],
    )

    if response.stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        category = getattr(details, "category", None) if details else None
        return Verdict.fail(f"the model declined the request (category: {category})")

    text = "".join(block.text for block in response.content if block.type == "text")
    if not text.strip():
        raise ValueError("the model returned an empty response")
    ctx.note(
        "model response received",
        task_id=task.id,
        model=response.model,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )
    return Verdict.parse(json.loads(text))

"""A worker that thinks with Grok, via xAI's OpenAI-compatible API.

This is the colony's primary model-backed worker: point a slot's `handler`
at it, give it an `XAI_API_KEY`, and the slot thinks with Grok. The slot's
description and context become the system prompt, the task's payload and
accumulated result become the message, and the verdict comes back as
structured output (`response_format={"type": "json_schema", ...}`), so the
return contract is checked by a schema instead of parsed out of text.

Requires `pip install grokklings[grok]` (the openai package, used only for
its OpenAI-compatible client) and `XAI_API_KEY` in the environment.
"""

from __future__ import annotations

import json
import os
from typing import Any

from ..model import Task, Verdict
from ..slots import WorkerContext, WorkerCrashed
from ._contract import VERDICT_SCHEMA, build_message, build_system

DEFAULT_MODEL = "grok-4.6"
DEFAULT_MAX_TOKENS = 16000
BASE_URL = "https://api.x.ai/v1"

_client = None


def _get_client() -> Any:
    global _client
    if _client is None:
        try:
            import openai
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on the environment
            raise WorkerCrashed(
                "the Grok worker needs the openai package: pip install 'grokklings[grok]'"
            ) from exc
        key = os.environ.get("XAI_API_KEY")
        if not key:
            raise WorkerCrashed("the Grok worker needs XAI_API_KEY in the environment")
        _client = openai.AsyncOpenAI(base_url=BASE_URL, api_key=key)
    return _client


async def handle(task: Task, ctx: WorkerContext) -> Verdict:
    client = _get_client()
    options = ctx.options
    model = options.get("model", DEFAULT_MODEL)
    # Give the client a bit less time than the slot: let the SDK raise its
    # own error instead of the slot's timeout cutting it off.
    request_timeout = max(5.0, ctx.slot.timeout - 5.0)

    response = await client.with_options(timeout=request_timeout).chat.completions.create(
        model=model,
        max_tokens=options.get("max_tokens", DEFAULT_MAX_TOKENS),
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "verdict", "schema": VERDICT_SCHEMA, "strict": True},
        },
        messages=[
            {"role": "system", "content": build_system(ctx)},
            {"role": "user", "content": build_message(task)},
        ],
    )

    choice = response.choices[0]
    text = choice.message.content
    if not text or not text.strip():
        raise ValueError("the model returned an empty response")
    ctx.note(
        "model response received",
        task_id=task.id,
        model=response.model,
        input_tokens=response.usage.prompt_tokens,
        output_tokens=response.usage.completion_tokens,
    )
    return Verdict.parse(json.loads(text))

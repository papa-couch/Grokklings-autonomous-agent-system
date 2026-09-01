"""The prompt and schema shared by every model-backed worker.

A model-backed handler builds the system prompt from the slot's description
and context, sends the task as the message, and gets the verdict back as
structured output, so the contract is checked by a schema instead of parsed
out of text. This module holds the part that's the same no matter which
model answers it; a handler module (`grok.py`, `anthropic.py`) only has to
wire up its own client.
"""

from __future__ import annotations

import json
from typing import Any

from ..model import Decision, Task
from ..slots import WorkerContext

VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": [d.value for d in Decision]},
        "result": {
            "type": "object",
            "description": "What this worker is adding to the task.",
            "additionalProperties": True,
        },
        "next_slot": {
            "type": ["string", "null"],
            "description": "The receiving slot. Required when decision=next, otherwise null.",
        },
        "reason": {
            "type": ["string", "null"],
            "description": "The reason. Required for decision=not_mine and decision=fail.",
        },
    },
    "required": ["decision", "result", "next_slot", "reason"],
    "additionalProperties": False,
}

CONTRACT = """You are a worker in a colony. You never call other workers: you
hand a decision back to the dispatcher, and it lays the route.

Return exactly one of:
- decision="next" + next_slot — the work is done, another slot should continue the task;
- decision="done" — there's nothing left to do on this task;
- decision="not_mine" + reason — this task isn't yours to handle;
- decision="fail" + reason — this task can't be completed.

Put only what you're adding into the result field. Prior workers' results
are already accumulated and are never overwritten."""


def build_system(ctx: WorkerContext) -> str:
    parts = [CONTRACT, f"\nYour slot: {ctx.slot.name}"]
    if ctx.slot.description:
        parts.append(f"What you do: {ctx.slot.description}")
    if ctx.slot.context:
        parts.append(f"\nInstructions, criteria, and thresholds:\n{ctx.slot.context}")
    if ctx.slot.tools:
        parts.append("\nTools available: " + ", ".join(ctx.slot.tools))
    if ctx.slot.can_route_to:
        parts.append("\nYou may hand off only to: " + ", ".join(ctx.slot.can_route_to))
    else:
        parts.append("\nThere's nowhere to hand off to — only done, not_mine, and fail are available.")
    return "\n".join(parts)


def build_message(task: Task) -> str:
    payload = json.dumps(task.payload, ensure_ascii=False, indent=2, default=str)
    blocks = [f"Task {task.id} from source “{task.source}”.", f"\nPayload:\n{payload}"]
    if task.result:
        accumulated = json.dumps(task.merged_result(), ensure_ascii=False, indent=2, default=str)
        blocks.append(f"\nAlready accumulated by prior workers:\n{accumulated}")
        blocks.append(f"\nRoute so far: {' -> '.join(task.route)}")
    return "\n".join(blocks)

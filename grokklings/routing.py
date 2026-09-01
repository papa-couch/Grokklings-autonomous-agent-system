"""Picking a slot when nobody named one.

A worker returns a verdict, not a destination; the dispatcher lays the
route. Most of the time the route is already known — the verdict named the
next slot, or there's a single entry slot. What was missing is the case
where nobody named anything: a task arrives and several slots could take
it, or a worker says "not mine" and someone else should look.

That's what a router decides. It's asked only when the answer isn't already
determined, so a colony with one entry slot and explicit handoffs never
pays for one.

A colony without a configured router behaves exactly as it did before: the
entry slot takes new tasks, and "not mine" goes to the unplaced pile.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol, runtime_checkable

from .model import SlotSpec, Task

log = logging.getLogger("grokklings.routing")

DEFAULT_MODEL = "grok-4.6"

PICK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "slot": {
            "type": ["string", "null"],
            "description": "The name of the slot to hand the task to, or null if none of them fit.",
        },
        "reason": {
            "type": ["string", "null"],
            "description": "Briefly, why that slot — or why none of them fit.",
        },
    },
    "required": ["slot", "reason"],
    "additionalProperties": False,
}

CONTRACT = """You are the dispatcher of a colony of workers. A task has come
in that nobody has claimed. Your only job is to say which slot should take
it, judging by what each slot says it does.

Pick the one slot that fits best. If none of them genuinely fit, return
slot=null — a wrong slot is worse than an honest "nobody." You are not
solving the task, and you are not inventing slots: return one of the names
listed, or null."""


@runtime_checkable
class Router(Protocol):
    """Picks one of the candidate slots for a task, or none of them."""

    async def pick(self, task: Task, candidates: list[SlotSpec]) -> str | None: ...


def describe(candidates: list[SlotSpec]) -> str:
    lines = []
    for spec in candidates:
        text = spec.description or "(no description)"
        lines.append(f"- {spec.name}: {text}")
    return "\n".join(lines)


def build_prompt(task: Task, candidates: list[SlotSpec]) -> str:
    payload = json.dumps(task.payload, ensure_ascii=False, indent=2, default=str)
    blocks = [f"Slots to choose from:\n{describe(candidates)}", f"\nThe task's payload:\n{payload}"]
    if task.result:
        accumulated = json.dumps(task.merged_result(), ensure_ascii=False, indent=2, default=str)
        blocks.append(f"\nWhat prior workers accumulated:\n{accumulated}")
    if task.route:
        blocks.append(f"\nWhere it's already been: {' -> '.join(task.route)}")
    if task.last_error:
        blocks.append(f"\nWhy it ended up unclaimed: {task.last_error}")
    return "\n".join(blocks)


class GrokRouter:
    """Asks Grok which slot fits, judging by the slots' descriptions.

    Needs the same key and package as the Grok worker
    (`pip install 'grokklings[grok]'`, `XAI_API_KEY`). A failed call is not a
    lost task: `pick` returns None, and the dispatcher falls back to the
    behavior of a colony with no router at all.
    """

    def __init__(self, model: str = DEFAULT_MODEL, max_tokens: int = 512, timeout: float = 30.0) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.timeout = timeout
        self._client: Any = None

    def client(self) -> Any:
        if self._client is None:
            from .handlers.grok import _get_client

            self._client = _get_client()
        return self._client

    async def pick(self, task: Task, candidates: list[SlotSpec]) -> str | None:
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0].name

        try:
            response = await self.client().with_options(timeout=self.timeout).chat.completions.create(
                model=self.model,
                max_tokens=self.max_tokens,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "slot_choice", "schema": PICK_SCHEMA, "strict": True},
                },
                messages=[
                    {"role": "system", "content": CONTRACT},
                    {"role": "user", "content": build_prompt(task, candidates)},
                ],
            )
            text = response.choices[0].message.content
            choice = json.loads(text) if text else {}
        except Exception as exc:  # the colony keeps running without a router
            log.warning("the router couldn't pick a slot for task %s: %r", task.id, exc)
            return None

        picked = choice.get("slot")
        allowed = {spec.name for spec in candidates}
        if picked in allowed:
            return picked
        if picked is not None:
            # Never trust a name that isn't on the list: a hallucinated slot
            # would land the task somewhere nobody offered.
            log.warning("the router named a slot outside the list: %r", picked)
        return None

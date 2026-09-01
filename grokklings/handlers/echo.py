"""Workers with no external dependencies: the skeleton is exercised without a model or the network."""

from __future__ import annotations

from typing import Any

from ..model import Task, Verdict
from ..slots import WorkerContext


def handle(task: Task, ctx: WorkerContext) -> Verdict:
    """Leaves a note in the result and says "done".

    Slot options:
      next   — return "next: <slot>" instead of "done"
      note   — what to add to the accumulated result
    """
    data: dict[str, Any] = {"note": ctx.options.get("note", f"handled by slot {ctx.slot.name}")}
    if ctx.options.get("echo_payload", True):
        data["payload"] = task.payload
    nxt = ctx.options.get("next")
    return Verdict.next(nxt, data) if nxt else Verdict.done(data)


def refuse(task: Task, ctx: WorkerContext) -> Verdict:
    """"Not mine" — the task goes back to the dispatcher as unplaced."""
    return Verdict.not_mine(ctx.options.get("reason", "this slot doesn't handle tasks like this"))

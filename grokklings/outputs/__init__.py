"""Output points.

An output is a regular worker, just a final one: same contract, same
retries, same journal entries. The user decides where it goes, in what
shape, and what to filter (`when` on the output's definition).
"""

from __future__ import annotations

from typing import Any

from ..model import Task


def envelope(task: Task, shape: str = "merged") -> dict[str, Any]:
    """The shape a task leaves the colony in."""
    body: dict[str, Any] = {
        "task": task.id,
        "source": task.source,
        "dedup_key": task.dedup_key,
        "route": task.route,
        "created_at": task.created_at,
    }
    if shape == "full":
        body["payload"] = task.payload
        body["result"] = [c.to_dict() for c in task.result]
    else:
        body["result"] = task.merged_result()
    return body

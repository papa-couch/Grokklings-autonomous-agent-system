"""Output to stdout — for debugging the colony."""

from __future__ import annotations

import json
import sys

from ..model import Task, Verdict
from ..slots import WorkerContext
from . import envelope


def handle(task: Task, ctx: WorkerContext) -> Verdict:
    body = envelope(task, ctx.options.get("shape", "merged"))
    print(json.dumps(body, ensure_ascii=False, indent=2, default=str), file=sys.stdout, flush=True)
    return Verdict.done({"delivered_to": "stdout"})

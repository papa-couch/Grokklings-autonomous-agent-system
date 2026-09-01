"""Output to a file: one task, one line of JSON."""

from __future__ import annotations

import json
import os

from ..model import Task, Verdict
from ..slots import WorkerContext
from . import envelope


def handle(task: Task, ctx: WorkerContext) -> Verdict:
    path = ctx.options.get("path")
    if not path:
        return Verdict.fail("this output has no path set")
    body = envelope(task, ctx.options.get("shape", "merged"))

    def append() -> str:
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(body, ensure_ascii=False, default=str) + "\n")
        return path

    # Appending isn't idempotent on its own: without this wrapper, a retry
    # would put a second identical line into the file.
    written = ctx.once("append", append)
    return Verdict.done({"delivered_to": written})

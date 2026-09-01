"""Intake: the single door into the system.

For every incoming item: assign an id, log the fact that it arrived, check
deduplication, then either send it to the shared queue or drop it with a
reference to the first instance.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .journal import Journal
from .model import Task, TaskStatus, new_id

WHOLE_PAYLOAD = "@payload"


@dataclass
class Admission:
    """What intake told the source."""

    task: Task
    accepted: bool
    duplicate_of: str | None = None


def extract_key(payload: Any, spec: str | None) -> str | None:
    """The deduplication key is supplied by the user: a contract address, a
    package name, a url.

    spec is a dotted path into the payload ("repo.url"), or "@payload" for a
    hash of the whole thing, or None — no deduplication.
    """
    if not spec:
        return None
    if spec == WHOLE_PAYLOAD:
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:32]
    node = payload
    for part in spec.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None  # no key found — the task passes through without dedup
    return None if node is None else str(node)


class Intake:
    def __init__(self, journal: Journal, dedup_key: str | None = WHOLE_PAYLOAD) -> None:
        self.journal = journal
        self.dedup_key = dedup_key

    def submit(self, source: str, payload: Any) -> Admission:
        task = Task(id=new_id(), source=source, payload=payload)
        task.dedup_key = extract_key(payload, self.dedup_key)
        self.journal.record("task_received", task.id, source=source, dedup_key=task.dedup_key)

        if task.dedup_key is not None:
            owner = self.journal.claim_dedup_key(task.dedup_key, task.id)
            if owner != task.id:
                task.status = TaskStatus.DROPPED
                task.duplicate_of = owner
                self.journal.enqueue(task)
                self.journal.record(
                    "task_duplicate", task.id, key=task.dedup_key, duplicate_of=owner
                )
                return Admission(task=task, accepted=False, duplicate_of=owner)

        self.journal.enqueue(task)
        self.journal.record("task_queued", task.id, key=task.dedup_key)
        return Admission(task=task, accepted=True)

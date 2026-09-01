"""The unit of work and the worker's return contract.

Mapping between the architecture doc's terms and the names in this code:
docs/glossary.md.
"""

from __future__ import annotations

import enum
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable


def now() -> float:
    return time.time()


def new_id() -> str:
    return uuid.uuid4().hex[:16]


class TaskStatus(str, enum.Enum):
    """Task status. A task never disappears — every outcome is a status."""

    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"
    DROPPED = "dropped"          # duplicate
    UNPLACED = "unplaced"        # unplaced pile: no slot claimed it
    LOOPED = "looped"            # stopped by the hop counter

    @property
    def terminal(self) -> bool:
        return self in _TERMINAL


_TERMINAL = frozenset(
    {TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.DROPPED, TaskStatus.LOOPED}
)


class Decision(str, enum.Enum):
    """What the worker handed back to the dispatcher."""

    NEXT = "next"          # next: <slot>
    DONE = "done"
    NOT_MINE = "not_mine"  # not mine + reason
    FAIL = "fail"          # fail + reason


@dataclass(frozen=True)
class Verdict:
    """The return contract. A worker never calls another worker directly —
    it only hands a decision back to the dispatcher."""

    decision: Decision
    result: Any = None
    next_slot: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.decision is Decision.NEXT and not self.next_slot:
            raise ValueError("a 'next' verdict must name a slot")
        if self.decision in (Decision.NOT_MINE, Decision.FAIL) and not self.reason:
            raise ValueError(f"a '{self.decision.value}' verdict must give a reason")

    # Constructors used by worker code.
    @staticmethod
    def next(slot: str, result: Any = None) -> "Verdict":
        return Verdict(Decision.NEXT, result=result, next_slot=slot)

    @staticmethod
    def done(result: Any = None) -> "Verdict":
        return Verdict(Decision.DONE, result=result)

    @staticmethod
    def not_mine(reason: str) -> "Verdict":
        return Verdict(Decision.NOT_MINE, reason=reason)

    @staticmethod
    def fail(reason: str) -> "Verdict":
        return Verdict(Decision.FAIL, reason=reason)

    @staticmethod
    def parse(raw: Any) -> "Verdict":
        """Parse a verdict that arrived as a dict (LLM worker, HTTP worker).

        Garbage input raises ValueError; the dispatcher treats that as a
        validation error and sends the task to retry.
        """
        if isinstance(raw, Verdict):
            return raw
        if not isinstance(raw, dict):
            raise ValueError(f"a verdict must be an object, got {type(raw).__name__}")
        value = raw.get("decision")
        try:
            decision = Decision(value)
        except ValueError:
            allowed = ", ".join(d.value for d in Decision)
            raise ValueError(f"unknown decision {value!r}, allowed: {allowed}") from None
        return Verdict(
            decision=decision,
            result=raw.get("result"),
            next_slot=raw.get("next_slot") or raw.get("next"),
            reason=raw.get("reason"),
        )


@dataclass
class Contribution:
    """One worker's contribution. Results are never overwritten, only accumulated."""

    slot: str
    instance: str
    at: float
    data: Any

    def to_dict(self) -> dict[str, Any]:
        return {"slot": self.slot, "instance": self.instance, "at": self.at, "data": self.data}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Contribution":
        return Contribution(slot=d["slot"], instance=d["instance"], at=d["at"], data=d.get("data"))


@dataclass
class Task:
    """The unit of work that travels through the system."""

    id: str
    source: str
    payload: Any
    dedup_key: str | None = None
    status: TaskStatus = TaskStatus.QUEUED
    slot: str | None = None                          # currently addressed to
    route: list[str] = field(default_factory=list)   # route: workers visited so far
    result: list[Contribution] = field(default_factory=list)  # accumulated result
    attempts: int = 0                                 # attempt counter for the current step
    hops: int = 0                                      # hop counter (loop guard)
    duplicate_of: str | None = None
    last_error: str | None = None
    created_at: float = field(default_factory=now)
    updated_at: float = field(default_factory=now)

    def add(self, slot: str, instance: str, data: Any) -> None:
        if data is None:
            return
        self.result.append(Contribution(slot=slot, instance=instance, at=now(), data=data))

    def visited(self, slot: str) -> bool:
        """A worker never gets the same task twice (except on an explicit retry)."""
        return slot in self.route

    def merged_result(self) -> dict[str, Any]:
        """Flat view of everything accumulated so far — for outputs and dashboards."""
        merged: dict[str, Any] = {}
        for c in self.result:
            merged.setdefault(c.slot, []).append(c.data)
        return merged

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "payload": json.dumps(self.payload, ensure_ascii=False),
            "dedup_key": self.dedup_key,
            "status": self.status.value,
            "slot": self.slot,
            "route": json.dumps(self.route, ensure_ascii=False),
            "result": json.dumps([c.to_dict() for c in self.result], ensure_ascii=False),
            "attempts": self.attempts,
            "hops": self.hops,
            "duplicate_of": self.duplicate_of,
            "last_error": self.last_error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_row(row: Iterable[Any] | dict[str, Any]) -> "Task":
        r = dict(row)
        return Task(
            id=r["id"],
            source=r["source"],
            payload=json.loads(r["payload"]),
            dedup_key=r["dedup_key"],
            status=TaskStatus(r["status"]),
            slot=r["slot"],
            route=json.loads(r["route"]),
            result=[Contribution.from_dict(c) for c in json.loads(r["result"])],
            attempts=r["attempts"],
            hops=r["hops"],
            duplicate_of=r["duplicate_of"],
            last_error=r["last_error"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )


@dataclass
class SlotSpec:
    """A slot's definition — filled in by the user."""

    name: str
    description: str = ""
    context: str = ""
    # What a good result from this slot looks like, in the user's words.
    # Read only when scoring; the worker never sees it.
    success: str = ""
    tools: list[str] = field(default_factory=list)
    handler: str = "grokklings.handlers.echo:handle"
    timeout: float = 60.0
    max_instances: int = 1
    can_route_to: list[str] = field(default_factory=list)
    entry: bool = False
    terminal: bool = False       # an output is a regular worker, just a final one
    max_attempts: int = 3
    options: dict[str, Any] = field(default_factory=dict)

    def may_route_to(self, slot: str) -> bool:
        return slot in self.can_route_to

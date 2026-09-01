"""A worker slot and its instances.

A slot is a definition (name, description, context, tools, timeout); an
instance is what actually picks up a task. In phase 1 the instance count is
static and equal to max_instances; spawning and collapsing are phase 3 —
add_instance/remove_instance are left here for that.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import inspect
from dataclasses import dataclass, field
from typing import Any, Callable

from .journal import Journal
from .model import SlotSpec, Task, Verdict, new_id


class WorkerError(Exception):
    """A failure on the worker's side. The dispatcher decides whether to retry or fail."""


class WorkerTimeout(WorkerError):
    pass


class WorkerCrashed(WorkerError):
    pass


class InvalidVerdict(WorkerError):
    """The worker returned garbage — a validation error, the task goes to retry."""


def resolve(path: str) -> Callable[..., Any]:
    """"package.module:function" -> callable."""
    if ":" not in path:
        raise ValueError(f"handler {path!r} must look like 'module:function'")
    module_name, attr = path.split(":", 1)
    module = importlib.import_module(module_name)
    try:
        return getattr(module, attr)
    except AttributeError:
        raise ValueError(f"module {module_name} has no {attr!r}") from None


@dataclass
class WorkerContext:
    """Everything a worker knows about itself. The slot's context — its
    instructions, criteria, and thresholds — arrives here from the user's config."""

    slot: SlotSpec
    instance: str
    journal: Journal
    attempt: int = 1
    task_id: str = ""

    @property
    def context(self) -> str:
        return self.slot.context

    @property
    def tools(self) -> list[str]:
        return self.slot.tools

    @property
    def options(self) -> dict[str, Any]:
        return self.slot.options

    def note(self, message: str, **data: Any) -> None:
        """Write to the journal on the instance's behalf."""
        self.journal.record(
            "worker_note", slot=self.slot.name, instance=self.instance, message=message, **data
        )

    # --------------------------------------------------------- non-repeatable steps

    def once(self, key: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run a piece of work at most once per task's lifetime.

        A retry doesn't redo what already succeeded: the result of a
        completed step is recorded, and a repeat returns it from the record.
        The key is the worker's choice, and it must stay the same across
        attempts.

            code = ctx.once("delivery", send_notification, body)

        This mechanism's boundary is an honest one: it closes the gap
        between "the step succeeded" and "the verdict was recorded". The gap
        inside the call itself — the external service already accepted the
        request, and we crashed before hearing back — isn't closed by this.
        That's what idempotency_key() is for.
        """
        recorded = self._recorded(key)
        if recorded is not Journal.MISSING:
            self.journal.record(
                "step_replayed", self.task_id, self.slot.name, key=key, instance=self.instance
            )
            return recorded
        result = fn(*args, **kwargs)
        self._remember(key, result)
        return result

    async def once_async(self, key: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """The same, for a coroutine worker."""
        recorded = self._recorded(key)
        if recorded is not Journal.MISSING:
            self.journal.record(
                "step_replayed", self.task_id, self.slot.name, key=key, instance=self.instance
            )
            return recorded
        result = await fn(*args, **kwargs)
        self._remember(key, result)
        return result

    def idempotency_key(self, name: str = "") -> str:
        """A stable key worth passing to an external service.

        The same across every attempt of one step, and different across
        tasks and slots. If the service honors it (an Idempotency-Key
        header, or a field of its own), it drops the repeat on its own —
        even the one we never learned about because we crashed before
        hearing back.
        """
        raw = f"{self._require_task()}|{self.slot.name}|{name}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def _require_task(self) -> str:
        if not self.task_id:
            raise RuntimeError(
                "this context was built without a task_id: non-repeatable steps are unavailable"
            )
        return self.task_id

    def _recorded(self, key: str) -> Any:
        return self.journal.step_result(self._require_task(), self.slot.name, key)

    def _remember(self, key: str, result: Any) -> None:
        self.journal.record_step(self.task_id, self.slot.name, key, result)
        self.journal.record(
            "step_completed", self.task_id, self.slot.name, key=key, instance=self.instance
        )


@dataclass
class Instance:
    """A slot instance. Local memory is phase 4 — the field is here already
    so collapsing has something to fold into shared memory."""

    id: str
    slot: str
    busy: bool = False
    handled: int = 0
    memory: dict[str, Any] = field(default_factory=dict)


class SlotRuntime:
    def __init__(self, spec: SlotSpec, journal: Journal, start: int | None = None) -> None:
        self.spec = spec
        self.journal = journal
        self.handler = resolve(spec.handler)
        self._is_async = inspect.iscoroutinefunction(self.handler)
        # Without scaling a slot starts at its ceiling and stays there —
        # `max_instances` is the whole story. With scaling the colony starts
        # low and grows toward that ceiling instead, so `start` overrides.
        wanted = spec.max_instances if start is None else start
        count = max(1, min(wanted, spec.max_instances))
        self.instances: list[Instance] = [
            Instance(id=f"{spec.name}#{i}", slot=spec.name) for i in range(count)
        ]

    @property
    def name(self) -> str:
        return self.spec.name

    def free_instance(self) -> Instance | None:
        for inst in self.instances:
            if not inst.busy:
                return inst
        return None

    def reserve(self) -> Instance | None:
        """Claim a free instance.

        The flag is set immediately, not whenever the coroutine finally gets
        a turn on the event loop — otherwise a single dispatcher pass could
        hand one instance several tasks. Whoever claims it also releases it,
        via release().
        """
        inst = self.free_instance()
        if inst is not None:
            inst.busy = True
        return inst

    @staticmethod
    def release(inst: Instance) -> None:
        inst.busy = False

    @property
    def load(self) -> int:
        return sum(1 for i in self.instances if i.busy)

    def add_instance(self) -> Instance:
        """Phase 3 extension point (spawning a clone)."""
        inst = Instance(id=f"{self.spec.name}#{new_id()[:6]}", slot=self.spec.name)
        self.instances.append(inst)
        self.journal.record("instance_spawned", slot=self.spec.name, instance=inst.id)
        return inst

    def remove_instance(self, inst: Instance) -> bool:
        """Phase 3 extension point (collapsing). The last instance, and one
        with a task in progress, are never removed."""
        if inst.busy or len(self.instances) <= 1:
            return False
        self.instances.remove(inst)
        self.journal.record("instance_collapsed", slot=self.spec.name, instance=inst.id)
        return True

    async def run(self, task: Task, instance: Instance, attempt: int) -> Verdict:
        """Run the task on the instance and return a validated verdict."""
        ctx = WorkerContext(
            slot=self.spec,
            instance=instance.id,
            journal=self.journal,
            attempt=attempt,
            task_id=task.id,
        )
        if self._is_async:
            coro = self.handler(task, ctx)
        else:
            coro = asyncio.to_thread(self.handler, task, ctx)
        try:
            raw = await asyncio.wait_for(coro, timeout=self.spec.timeout)
        except asyncio.TimeoutError:
            # An async worker is cancelled at this point; a sync one keeps
            # running to completion in its own thread — there's nothing to
            # interrupt it with, so give such slots a generous timeout.
            raise WorkerTimeout(f"slot {self.spec.name}: timed out after {self.spec.timeout}s") from None
        except WorkerError:
            raise
        except Exception as exc:
            raise WorkerCrashed(f"slot {self.spec.name}: {type(exc).__name__}: {exc}") from exc
        try:
            verdict = Verdict.parse(raw)
        except ValueError as exc:
            raise InvalidVerdict(str(exc)) from exc
        instance.handled += 1
        return verdict

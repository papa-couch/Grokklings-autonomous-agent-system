"""The dispatcher: the only one who decides who gets a task.

A worker never calls another worker directly — it returns a verdict, and the
dispatcher lays the route. That's what removes the need for a separate
router character, and keeps the swarm from turning into spaghetti.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from .cost import Guard
from .journal import Journal
from .model import Decision, SlotSpec, Task, TaskStatus, Verdict
from .routing import Router
from .scaling import Scaler
from .slots import Instance, InvalidVerdict, SlotRuntime, WorkerError, resolve

log = logging.getLogger("grokklings.dispatcher")

DEFAULT_MAX_HOPS = 10
LEASE_MARGIN = 30.0


class Dispatcher:
    def __init__(
        self,
        journal: Journal,
        slots: dict[str, SlotRuntime],
        entry: str | None = None,
        outputs: list[str] | None = None,
        max_hops: int = DEFAULT_MAX_HOPS,
        retry_base: float = 1.0,
        poll_interval: float = 0.05,
        recover_every: float = 15.0,
        on_alert: Callable[[str, dict[str, Any]], None] | None = None,
        router: "Router | None" = None,
        entries: list[str] | None = None,
        scaler: "Scaler | None" = None,
        guard: "Guard | None" = None,
    ) -> None:
        self.scaler = scaler
        self.guard = guard
        self.journal = journal
        self.slots = slots
        self.entry = entry
        # Several slots can be marked entry only when a router is configured
        # — otherwise there'd be nobody to choose between them.
        self.entries = entries or ([entry] if entry else [])
        self.router = router
        self.outputs = outputs or []
        self.max_hops = max_hops
        self.retry_base = retry_base
        self.poll_interval = poll_interval
        self.recover_every = recover_every
        self.on_alert = on_alert or (lambda kind, data: log.warning("alert: %s %s", kind, data))
        self.paused = False
        self._inflight: set[asyncio.Task[None]] = set()
        self._last_recover = 0.0

    # ------------------------------------------------------------------- loop

    async def run(self, stop: asyncio.Event | None = None) -> None:
        stop = stop or asyncio.Event()
        self.journal.record("colony_started", slots=list(self.slots), entry=self.entry, outputs=self.outputs)
        for task in self.journal.recover_expired():
            log.info("task %s put back on the queue after a restart", task.id)
        try:
            while not stop.is_set():
                await self.tick()
                await asyncio.sleep(self.poll_interval)
        finally:
            # Pause/stop: no new tasks are taken, in-flight ones are seen through.
            pending = len(self._inflight)
            if pending:
                await asyncio.gather(*self._inflight, return_exceptions=True)
            self.journal.record("colony_stopped", finished_on_stop=pending)

    async def tick(self) -> int:
        """One pass over the queue. Returns how many tasks were launched."""
        loop_now = asyncio.get_running_loop().time()
        if loop_now - self._last_recover >= self.recover_every:
            self._last_recover = loop_now
            self.journal.recover_expired()
        # Checked before the pause test, so a colony already over budget
        # stays paused rather than quietly resuming on the next tick.
        if self.guard is not None and not self.paused:
            reason = self.guard.over(loop_now)
            if reason is not None:
                self.paused = True
                self.journal.record("cost_limit_reached", None, None, reason=reason)
                self.on_alert("cost_limit_reached", {"reason": reason})
                log.warning("colony paused: %s", reason)

        if self.paused:
            return 0

        if self.scaler is not None:
            # Before handing out work, not after: a clone spawned now can
            # take a task on this same pass.
            self.scaler.tick(self.slots, loop_now)

        launched = 0
        for task in self.journal.peek():
            target = await self.route(task)
            if target is None:
                continue
            runtime = self.slots[target]
            instance = runtime.reserve()
            if instance is None:
                continue  # the slot is fully busy — the task waits its turn
            task.slot = target
            if not task.visited(target):
                task.route.append(target)
            task.attempts += 1
            if not self.journal.claim(task, lease=runtime.spec.timeout + LEASE_MARGIN):
                runtime.release(instance)
                continue  # someone else got there first
            self.journal.record(
                "task_assigned", task.id, target, instance=instance.id, attempt=task.attempts
            )
            job = asyncio.create_task(self._execute(task, runtime, instance))
            self._inflight.add(job)
            # Released here, not inside the coroutine: a job can be cancelled
            # before it ever starts, and then the instance would stay busy
            # forever.
            job.add_done_callback(lambda done, rt=runtime, inst=instance: self._job_done(done, rt, inst))
            launched += 1
        return launched

    def _job_done(self, job: "asyncio.Task[None]", runtime: SlotRuntime, instance: Instance) -> None:
        self._inflight.discard(job)
        runtime.release(instance)
        if not job.cancelled() and job.exception() is not None:
            # Only a failure in the dispatcher itself lands here — worker
            # failures are handled inside. The task stays in progress and
            # returns to the queue once its lease expires; it can't be lost.
            log.error("processing failure in slot %s: %r", runtime.name, job.exception())

    async def drain(self) -> None:
        """Wait for tasks that are already in flight."""
        while self._inflight:
            await asyncio.gather(*list(self._inflight), return_exceptions=True)

    # ------------------------------------------------------------------ routing

    async def route(self, task: Task) -> str | None:
        """Determine the slot for a task.

        In order: the slot a previous verdict named; a single entry slot;
        and only when neither settles it — the router, picking among the
        entry slots by what they say they do. A colony with one entry slot
        never reaches the router, so it never pays for a call it doesn't
        need.
        """
        if task.slot and task.slot in self.slots:
            return task.slot
        if task.slot and task.slot not in self.slots:
            self._unplaced(task, f"slot {task.slot!r} doesn't exist in this colony")
            return None

        known = [name for name in self.entries if name in self.slots]
        if len(known) == 1:
            return known[0]
        if not known:
            self._unplaced(task, "no entry slot configured")
            return None

        picked = await self._pick(task, known, "entry")
        if picked is None:
            self._unplaced(task, "the router didn't pick an entry slot")
        return picked

    async def _pick(self, task: Task, candidates: list[str], purpose: str) -> str | None:
        """Ask the router to choose among candidates. None means nobody fit."""
        if self.router is None or not candidates:
            return None
        specs: list[SlotSpec] = [self.slots[name].spec for name in candidates]
        try:
            picked = await self.router.pick(task, specs)
        except Exception as exc:
            # A router is user-supplied code. If it throws, the task falls
            # back to what a colony without a router would do — it doesn't
            # sit in progress waiting out its lease, over and over.
            log.warning("the router failed on task %s: %r", task.id, exc)
            self.journal.record(
                "slot_pick_failed", task.id, None, purpose=purpose, error=f"{type(exc).__name__}: {exc}"
            )
            return None
        if picked is not None and picked not in self.slots:
            log.warning("the router named a slot that doesn't exist: %r", picked)
            picked = None
        self.journal.record(
            "slot_picked", task.id, picked, purpose=purpose, candidates=candidates
        )
        return picked

    def _pending_outputs(self, task: Task) -> list[str]:
        """Outputs that still need to receive this task."""
        pending = []
        for name in self.outputs:
            runtime = self.slots.get(name)
            if runtime is None or task.visited(name):
                continue
            if self._output_accepts(runtime, task):
                pending.append(name)
        return pending

    @staticmethod
    def _output_accepts(runtime: SlotRuntime, task: Task) -> bool:
        """What an output filters on is up to the user."""
        when = runtime.spec.options.get("when") or {}
        required = when.get("route_contains") or []
        if required and not all(slot in task.route for slot in required):
            return False
        predicate = when.get("filter")
        if predicate:
            return bool(resolve(predicate)(task))
        return True

    # -------------------------------------------------------------------- work

    async def _execute(self, task: Task, runtime: SlotRuntime, instance: Instance) -> None:
        try:
            verdict = await runtime.run(task, instance, attempt=task.attempts)
        except WorkerError as exc:
            self._retry_or_fail(task, runtime, exc)
            return
        except asyncio.CancelledError:
            self.journal.requeue(task)
            self.journal.record("task_returned", task.id, runtime.name, reason="execution cancelled")
            raise
        self.journal.record(
            "worker_result",
            task.id,
            runtime.name,
            instance=instance.id,
            decision=verdict.decision.value,
            next_slot=verdict.next_slot,
            reason=verdict.reason,
        )
        await self._apply(task, verdict, runtime, instance)

    async def _apply(self, task: Task, verdict: Verdict, runtime: SlotRuntime, instance: Instance) -> None:
        # Results are never overwritten, only accumulated: everyone adds their own.
        task.add(runtime.name, instance.id, verdict.result)

        if verdict.decision is Decision.NEXT:
            self._go_next(task, verdict.next_slot or "", runtime)
        elif verdict.decision is Decision.DONE:
            self._complete(task, runtime)
        elif verdict.decision is Decision.NOT_MINE:
            await self._rehome(task, runtime, verdict.reason or "not mine")
        else:  # Decision.FAIL — a deliberate refusal by the worker, not a crash
            task.last_error = verdict.reason
            self.journal.finish(task, TaskStatus.FAILED)
            self.journal.record("task_failed", task.id, runtime.name, reason=verdict.reason)
            self.on_alert("task_failed", {"task": task.id, "slot": runtime.name, "reason": verdict.reason})

    def _go_next(self, task: Task, target: str, runtime: SlotRuntime) -> None:
        if target not in self.slots:
            self._retry_or_fail(task, runtime, InvalidVerdict(f"slot {target!r} doesn't exist in this colony"))
            return
        if runtime.spec.can_route_to and not runtime.spec.may_route_to(target):
            self._retry_or_fail(
                task, runtime, InvalidVerdict(f"slot {runtime.name} isn't allowed to hand off to {target}")
            )
            return
        if task.visited(target):
            # A worker never gets the same task twice, except on an explicit retry.
            self._stop_loop(task, f"revisited {target}")
            return
        if task.hops + 1 > self.max_hops:
            self._stop_loop(task, f"hop limit exceeded ({self.max_hops})")
            return
        self._transition(task, target)

    async def _rehome(self, task: Task, runtime: SlotRuntime, reason: str) -> None:
        """"Not mine" — ask the router whether anyone else fits.

        Without a router this is what it always was: the unplaced pile. With
        one, a refusal becomes a handoff, and only a task nobody will take
        lands on the pile.
        """
        task.last_error = reason
        candidates = [
            name
            for name, rt in self.slots.items()
            if not rt.spec.terminal and name != runtime.name and not task.visited(name)
        ]
        picked = await self._pick(task, candidates, "not_mine")
        if picked is None:
            self._unplaced(task, reason)
            return
        if task.hops + 1 > self.max_hops:
            self._stop_loop(task, f"hop limit exceeded ({self.max_hops}) after a refusal")
            return
        self.journal.record("task_rehomed", task.id, picked, refused_by=runtime.name, reason=reason)
        self._transition(task, picked)

    def _complete(self, task: Task, runtime: SlotRuntime) -> None:
        """"Done" from a regular worker sends the task to an output;
        "done" from an output finishes it."""
        pending = self._pending_outputs(task)
        if not pending:
            self.journal.finish(task, TaskStatus.DONE)
            self.journal.record("task_done", task.id, runtime.name, contributions=len(task.result))
            return
        if task.hops + 1 > self.max_hops:
            self._stop_loop(task, f"hop limit exceeded ({self.max_hops}) on the way to an output")
            return
        self._transition(task, pending[0])

    def _transition(self, task: Task, target: str) -> None:
        task.hops += 1
        task.slot = target
        task.attempts = 0  # the attempt counter is per step, not per task
        self.journal.requeue(task)
        self.journal.record("task_routed", task.id, target, hops=task.hops, route=task.route)

    # ------------------------------------------------------------------ failures

    def _retry_or_fail(self, task: Task, runtime: SlotRuntime, exc: Exception) -> None:
        task.last_error = f"{type(exc).__name__}: {exc}"
        if task.attempts < runtime.spec.max_attempts:
            delay = self.retry_base * (2 ** (task.attempts - 1))  # exponential backoff
            # The task goes back to the same slot — this is an explicit
            # retry, so its trace is dropped from the route.
            if task.route and task.route[-1] == runtime.name:
                task.route.pop()
            self.journal.requeue(task, delay=delay)
            self.journal.record(
                "retry_scheduled", task.id, runtime.name,
                attempt=task.attempts, delay=delay, error=task.last_error,
            )
            return
        self.journal.finish(task, TaskStatus.FAILED)
        self.journal.record(
            "task_failed", task.id, runtime.name, attempts=task.attempts, error=task.last_error
        )
        self.on_alert("task_failed", {"task": task.id, "slot": runtime.name, "error": task.last_error})

    def _unplaced(self, task: Task, reason: str) -> None:
        """The unplaced pile: its size is the input for the brain (phase 5)."""
        task.last_error = reason
        self.journal.finish(task, TaskStatus.UNPLACED)
        self.journal.record("task_unplaced", task.id, task.slot, reason=reason)
        self.on_alert("unplaced", {"task": task.id, "reason": reason})

    def _stop_loop(self, task: Task, reason: str) -> None:
        task.last_error = reason
        self.journal.finish(task, TaskStatus.LOOPED)
        self.journal.record("task_looped", task.id, task.slot, reason=reason, route=task.route)
        self.on_alert("looped", {"task": task.id, "reason": reason, "route": task.route})

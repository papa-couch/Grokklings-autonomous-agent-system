"""Slots: the instances that do the work, and the contract around them.

The dispatcher is tested end-to-end elsewhere. What that leaves unpinned is
the layer under it, where two mistakes are both silent. An instance whose
`busy` flag is set a moment too late gets handed several tasks in a single
dispatcher pass and nothing anywhere says so. A worker error that reaches
the dispatcher wearing the wrong class gets retried when it should have
failed, or failed when it should have been retried — and the difference
only shows up as work that quietly never finishes.

The rest is the boundary around `once`, which is the promise that a retry
doesn't send the same notification twice.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

from grokklings.journal import Journal
from grokklings.model import SlotSpec, Task, Verdict
from grokklings.slots import (
    InvalidVerdict,
    SlotRuntime,
    WorkerContext,
    WorkerCrashed,
    WorkerError,
    WorkerTimeout,
    resolve,
)


class TestResolve(unittest.TestCase):
    """A typo in a handler path is the first thing a new user gets wrong."""

    def test_a_path_without_a_colon_is_refused_by_name(self):
        with self.assertRaises(ValueError) as caught:
            resolve("tests.workers.done")
        self.assertIn("module:function", str(caught.exception))

    def test_a_missing_function_names_the_function(self):
        with self.assertRaises(ValueError) as caught:
            resolve("tests.workers:no_such_worker")
        self.assertIn("no_such_worker", str(caught.exception))

    def test_a_missing_module_raises_the_import_error(self):
        """Not swallowed into a ValueError: the traceback is the useful part."""
        with self.assertRaises(ModuleNotFoundError):
            resolve("no.such.module:done")

    def test_a_real_handler_comes_back_callable(self):
        self.assertTrue(callable(resolve("tests.workers:done")))


class SlotCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="grokklings-slots-"))
        self.journal = Journal(str(self.dir / "colony.db"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.addCleanup(self.journal.close)

    def runtime(self, handler: str = "tests.workers:done", **extra: Any) -> SlotRuntime:
        start = extra.pop("start", None)
        spec = SlotSpec(name="triage", handler=f"tests.workers:{handler}"
                        if ":" not in handler else handler, **extra)
        return SlotRuntime(spec, self.journal, start=start)

    def task(self, task_id: str = "t1") -> Task:
        return Task(id=task_id, source="manual", payload={"url": "https://a"})


class TestInstancePool(SlotCase):
    def test_a_slot_starts_at_its_ceiling_when_nothing_scales_it(self):
        self.assertEqual(len(self.runtime(max_instances=3).instances), 3)

    def test_scaling_starts_it_low_instead(self):
        self.assertEqual(len(self.runtime(max_instances=5, start=2).instances), 2)

    def test_the_ceiling_still_wins_over_a_larger_start(self):
        self.assertEqual(len(self.runtime(max_instances=2, start=9).instances), 2)

    def test_a_slot_always_has_at_least_one_instance(self):
        """A slot with nothing in it is a slot that silently drops its queue."""
        self.assertEqual(len(self.runtime(max_instances=3, start=0).instances), 1)

    def test_reserving_marks_the_instance_busy_right_away(self):
        """Not when the coroutine next gets a turn — a single dispatcher pass
        reserves several times before anything is awaited, and a late flag
        would hand one instance the whole batch."""
        runtime = self.runtime(max_instances=2)
        first = runtime.reserve()
        second = runtime.reserve()

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first.id, second.id)
        self.assertIsNone(runtime.reserve())

    def test_releasing_hands_the_instance_back(self):
        runtime = self.runtime(max_instances=1)
        inst = runtime.reserve()
        self.assertIsNone(runtime.reserve())

        SlotRuntime.release(inst)
        self.assertIsNotNone(runtime.reserve())

    def test_load_counts_only_the_busy_ones(self):
        runtime = self.runtime(max_instances=3)
        runtime.reserve()
        runtime.reserve()
        self.assertEqual(runtime.load, 2)


class TestScalingTheSlot(SlotCase):
    def test_a_spawned_clone_is_written_down(self):
        runtime = self.runtime(max_instances=3, start=1)
        inst = runtime.add_instance()

        self.assertEqual(len(runtime.instances), 2)
        events = self.journal.events(type="instance_spawned")
        self.assertEqual(events[0]["data"]["instance"], inst.id)

    def test_a_clone_gets_its_own_name(self):
        runtime = self.runtime(max_instances=3, start=1)
        names = {runtime.add_instance().id for _ in range(3)}
        self.assertEqual(len(names), 3)

    def test_a_busy_instance_is_never_collapsed(self):
        """Collapsing one mid-task would drop the task with no record of it."""
        runtime = self.runtime(max_instances=3, start=2)
        inst = runtime.reserve()

        self.assertFalse(runtime.remove_instance(inst))
        self.assertIn(inst, runtime.instances)

    def test_the_last_instance_is_never_collapsed(self):
        runtime = self.runtime(max_instances=3, start=1)
        self.assertFalse(runtime.remove_instance(runtime.instances[0]))

    def test_collapsing_an_idle_clone_is_written_down(self):
        runtime = self.runtime(max_instances=3, start=2)
        inst = runtime.instances[-1]

        self.assertTrue(runtime.remove_instance(inst))
        self.assertNotIn(inst, runtime.instances)
        events = self.journal.events(type="instance_collapsed")
        self.assertEqual(events[0]["data"]["instance"], inst.id)


class TestRunning(SlotCase):
    async def run_one(self, handler: str, **extra: Any) -> Verdict:
        runtime = self.runtime(handler, **extra)
        return await runtime.run(self.task(), runtime.instances[0], attempt=1)

    async def test_a_verdict_comes_back_parsed(self):
        verdict = await self.run_one("done")
        self.assertEqual(verdict.result["seen"], {"url": "https://a"})

    async def test_a_crash_is_wrapped_with_the_slot_and_the_cause(self):
        """The dispatcher retries on this. The message is what a user reads
        in the journal when they ask why."""
        with self.assertRaises(WorkerCrashed) as caught:
            await self.run_one("crash", options={"succeed_on": 99})
        message = str(caught.exception)
        self.assertIn("triage", message)
        self.assertIn("RuntimeError", message)
        self.assertIn("the instance crashed", message)

    async def test_a_hang_becomes_a_timeout_not_a_crash(self):
        """Different classes because they mean different things to the
        dispatcher: one is the worker's fault, one is the clock's."""
        with self.assertRaises(WorkerTimeout) as caught:
            await self.run_one("hang", timeout=0.05)
        self.assertIn("0.05", str(caught.exception))

    async def test_garbage_instead_of_a_verdict_is_its_own_error(self):
        with self.assertRaises(InvalidVerdict):
            await self.run_one("garbage")

    async def test_a_deliberate_worker_error_passes_through_unchanged(self):
        """A worker that raises WorkerCrashed on purpose — the Grok backend
        does, for a missing key — must not be re-wrapped into a nested
        message nobody can read."""
        async def deliberate(task: Task, ctx: WorkerContext) -> Verdict:
            raise WorkerCrashed("the Grok worker needs XAI_API_KEY in the environment")

        spec = SlotSpec(name="triage", handler="tests.workers:done")
        runtime = SlotRuntime(spec, self.journal)
        runtime.handler = deliberate
        runtime._is_async = True

        with self.assertRaises(WorkerCrashed) as caught:
            await runtime.run(self.task(), runtime.instances[0], attempt=1)
        self.assertEqual(str(caught.exception), "the Grok worker needs XAI_API_KEY in the environment")

    async def test_a_sync_worker_does_not_block_the_event_loop(self):
        """Sync handlers go to a thread. If they didn't, one slow worker
        would stop every other instance in the colony."""
        import time

        def slow(task: Task, ctx: WorkerContext) -> Verdict:
            time.sleep(0.15)
            return Verdict.done({})

        spec = SlotSpec(name="triage", handler="tests.workers:done", timeout=5)
        runtime = SlotRuntime(spec, self.journal)
        runtime.handler = slow
        runtime._is_async = False

        ticks = 0

        async def count() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        counter = asyncio.create_task(count())
        await runtime.run(self.task(), runtime.instances[0], attempt=1)
        counter.cancel()

        self.assertGreater(ticks, 3, "the loop was blocked while the worker ran")

    async def test_the_handled_count_rises_on_success(self):
        runtime = self.runtime("done")
        inst = runtime.instances[0]
        await runtime.run(self.task(), inst, attempt=1)
        self.assertEqual(inst.handled, 1)

    async def test_a_crash_is_not_counted_as_handled(self):
        """The scaler reads this. Counting failures as throughput would have
        it grow the colony in response to a broken worker."""
        runtime = self.runtime("crash", options={"succeed_on": 99})
        inst = runtime.instances[0]
        with self.assertRaises(WorkerError):
            await runtime.run(self.task(), inst, attempt=1)
        self.assertEqual(inst.handled, 0)

    async def test_garbage_is_not_counted_as_handled_either(self):
        """The worker returned, so this is the case where the count is easy
        to increment a line too early. It didn't produce a usable verdict —
        that is not throughput."""
        runtime = self.runtime("garbage")
        inst = runtime.instances[0]
        with self.assertRaises(InvalidVerdict):
            await runtime.run(self.task(), inst, attempt=1)
        self.assertEqual(inst.handled, 0)

    async def test_the_attempt_number_reaches_the_worker(self):
        seen: list[int] = []

        def peek(task: Task, ctx: WorkerContext) -> Verdict:
            seen.append(ctx.attempt)
            return Verdict.done({})

        spec = SlotSpec(name="triage", handler="tests.workers:done")
        runtime = SlotRuntime(spec, self.journal)
        runtime.handler = peek
        runtime._is_async = False

        await runtime.run(self.task(), runtime.instances[0], attempt=3)
        self.assertEqual(seen, [3])


class TestWorkerContext(SlotCase):
    def context(self, task_id: str = "t1", **extra: Any) -> WorkerContext:
        spec = SlotSpec(name="triage", handler="tests.workers:done",
                        context="Drop the junk.", tools=["fetch"],
                        options={"model": "grok-4.6"}, **extra)
        return WorkerContext(slot=spec, instance="triage#0", journal=self.journal, task_id=task_id)

    def test_the_slots_config_reaches_the_worker(self):
        ctx = self.context()
        self.assertEqual(ctx.context, "Drop the junk.")
        self.assertEqual(ctx.tools, ["fetch"])
        self.assertEqual(ctx.options["model"], "grok-4.6")

    def test_a_note_lands_in_the_journal_under_the_slot(self):
        self.context().note("looked it up", found=3)
        event = self.journal.events(type="worker_note")[0]

        self.assertEqual(event["slot"], "triage")
        self.assertEqual(event["data"]["message"], "looked it up")
        self.assertEqual(event["data"]["found"], 3)


class TestOnce(SlotCase):
    """The promise a retry rests on: the outside world gets touched once."""

    def context(self, task_id: str = "t1") -> WorkerContext:
        spec = SlotSpec(name="triage", handler="tests.workers:done")
        return WorkerContext(slot=spec, instance="triage#0", journal=self.journal, task_id=task_id)

    def test_a_completed_step_is_not_run_again(self):
        ctx = self.context()
        runs = []
        self.assertEqual(ctx.once("send", lambda: runs.append(1) or "ok"), "ok")
        self.assertEqual(ctx.once("send", lambda: runs.append(1) or "ok"), "ok")
        self.assertEqual(len(runs), 1)

    def test_a_step_that_returned_nothing_is_still_not_run_again(self):
        """`None` is the honest answer of a function that just does a thing.
        Reading it as "never ran" would resend on every retry."""
        ctx = self.context()
        runs = []
        ctx.once("send", lambda: runs.append(1))
        ctx.once("send", lambda: runs.append(1))
        self.assertEqual(len(runs), 1)

    def test_a_replay_is_written_down(self):
        ctx = self.context()
        ctx.once("send", lambda: "ok")
        ctx.once("send", lambda: "ok")
        self.assertEqual(len(self.journal.events(type="step_replayed")), 1)
        self.assertEqual(len(self.journal.events(type="step_completed")), 1)

    def test_a_step_that_raised_is_not_recorded(self):
        """It didn't happen, so the retry must actually retry it."""
        ctx = self.context()

        def boom():
            raise RuntimeError("the step failed")

        with self.assertRaises(RuntimeError):
            ctx.once("send", boom)
        self.assertEqual(self.journal.events(type="step_completed"), [])

    def test_different_keys_are_different_steps(self):
        ctx = self.context()
        runs = []
        ctx.once("first", lambda: runs.append("a"))
        ctx.once("second", lambda: runs.append("b"))
        self.assertEqual(runs, ["a", "b"])

    def test_another_task_does_not_inherit_the_step(self):
        self.context("t1").once("send", lambda: "ok")
        runs = []
        self.context("t2").once("send", lambda: runs.append(1) or "ok")
        self.assertEqual(len(runs), 1)

    def test_arguments_reach_the_step(self):
        self.assertEqual(self.context().once("send", lambda a, b=0: a + b, 2, b=3), 5)

    async def test_the_coroutine_form_behaves_the_same(self):
        ctx = self.context()
        runs = []

        async def effect() -> str:
            runs.append(1)
            return "ok"

        self.assertEqual(await ctx.once_async("send", effect), "ok")
        self.assertEqual(await ctx.once_async("send", effect), "ok")
        self.assertEqual(len(runs), 1)

    def test_a_context_without_a_task_says_what_is_wrong(self):
        """Reachable from a hand-built context — the Grok worker's tests do
        exactly that. A bare KeyError here would send someone hunting."""
        spec = SlotSpec(name="triage", handler="tests.workers:done")
        ctx = WorkerContext(slot=spec, instance="triage#0", journal=self.journal)

        with self.assertRaises(RuntimeError) as caught:
            ctx.once("send", lambda: "ok")
        self.assertIn("non-repeatable steps are unavailable", str(caught.exception))


class TestIdempotencyKey(SlotCase):
    """For the gap `once` can't close: the service accepted the request and
    we died before hearing back."""

    def key(self, task_id: str = "t1", slot: str = "triage", name: str = "") -> str:
        spec = SlotSpec(name=slot, handler="tests.workers:done")
        ctx = WorkerContext(slot=spec, instance=f"{slot}#0", journal=self.journal, task_id=task_id)
        return ctx.idempotency_key(name)

    def test_the_key_is_the_same_across_attempts(self):
        self.assertEqual(self.key(), self.key())

    def test_a_different_task_gets_a_different_key(self):
        self.assertNotEqual(self.key("t1"), self.key("t2"))

    def test_a_different_slot_gets_a_different_key(self):
        self.assertNotEqual(self.key(slot="triage"), self.key(slot="deep_dive"))

    def test_a_different_step_gets_a_different_key(self):
        self.assertNotEqual(self.key(name="send"), self.key(name="post"))

    def test_the_key_is_short_enough_for_a_header(self):
        key = self.key()
        self.assertEqual(len(key), 32)
        self.assertTrue(key.isalnum())

    def test_no_task_means_no_key(self):
        spec = SlotSpec(name="triage", handler="tests.workers:done")
        ctx = WorkerContext(slot=spec, instance="triage#0", journal=self.journal)
        with self.assertRaises(RuntimeError):
            ctx.idempotency_key()


if __name__ == "__main__":
    unittest.main()

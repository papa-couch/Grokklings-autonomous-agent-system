from __future__ import annotations

import unittest

from grokklings.model import TaskStatus
from tests import workers
from tests.helpers import ColonyFixture, slot


class DispatcherCase(unittest.IsolatedAsyncioTestCase):
    fixture: ColonyFixture

    def setUp(self):
        workers.reset()

    def tearDown(self):
        if getattr(self, "fixture", None):
            self.fixture.close()

    def build(self, **raw):
        self.fixture = ColonyFixture(raw)
        return self.fixture

    async def run_one(self, payload=None):
        task = self.fixture.colony.submit(payload or {"url": "https://a"}).task
        await self.fixture.settle()
        return self.fixture.colony.journal.get(task.id)


class TestVerdicts(DispatcherCase):
    async def test_done_goes_to_output_and_accumulates_result(self):
        self.build(
            slot=[slot("triage", "done")],
            output=[{"name": "output", "handler": "tests.workers:done"}],
        )
        task = await self.run_one()

        self.assertEqual(task.status, TaskStatus.DONE)
        self.assertEqual(task.route, ["triage", "output"])
        # Each worker added its own contribution, nothing got overwritten.
        self.assertEqual([c.slot for c in task.result], ["triage", "output"])

    async def test_next_routes_between_slots(self):
        self.build(
            slot=[
                slot("first", "route_next", entry=True, can_route_to=["second"], next="second"),
                slot("second", "done"),
            ],
            output=[{"name": "output", "handler": "tests.workers:done"}],
        )
        task = await self.run_one()

        self.assertEqual(task.status, TaskStatus.DONE)
        self.assertEqual(task.route, ["first", "second", "output"])
        self.assertEqual(task.hops, 2)

    async def test_not_mine_lands_in_the_unplaced_pile(self):
        self.build(slot=[slot("triage", "refuse")])
        task = await self.run_one()

        self.assertEqual(task.status, TaskStatus.UNPLACED)
        self.assertIn("not my part of it", task.last_error)

    async def test_fail_is_terminal_and_journalled(self):
        self.build(slot=[slot("triage", "fail")])
        task = await self.run_one()

        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(workers.calls["triage"], 1)  # a deliberate refusal isn't retried
        types = [e["type"] for e in self.fixture.colony.journal.events(task_id=task.id)]
        self.assertIn("task_failed", types)


class TestFailureHandling(DispatcherCase):
    async def test_crash_is_retried_until_it_succeeds(self):
        self.build(slot=[slot("triage", "crash", max_attempts=3, succeed_on=3)])
        task = await self.run_one()

        self.assertEqual(task.status, TaskStatus.DONE)
        self.assertEqual(workers.calls["triage"], 3)
        delays = [
            e["data"]["delay"]
            for e in self.fixture.colony.journal.events(task_id=task.id, type="retry_scheduled")
        ]
        self.assertEqual(len(delays), 2)
        self.assertLess(delays[0], delays[1])  # the backoff grows exponentially

    async def test_exhausted_attempts_end_in_failure_not_in_loss(self):
        self.build(slot=[slot("triage", "crash", max_attempts=3, succeed_on=99)])
        task = await self.run_one()

        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(workers.calls["triage"], 3)
        self.assertIn("the instance crashed", task.last_error)

    async def test_timeout_is_retried(self):
        self.build(slot=[slot("triage", "hang", timeout=0.05, max_attempts=2)])
        task = await self.run_one()

        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(workers.calls["triage"], 2)
        self.assertIn("timed out", task.last_error)

    async def test_garbage_verdict_is_a_validation_error(self):
        self.build(slot=[slot("triage", "garbage", max_attempts=2)])
        task = await self.run_one()

        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertIn("unknown decision", task.last_error)

    async def test_crashed_instance_returns_the_task_to_the_queue(self):
        self.build(slot=[slot("triage", "done")])
        journal = self.fixture.colony.journal
        task = self.fixture.colony.submit({"url": "https://a"}).task

        # The instance took the task and died without returning a verdict.
        task.slot = "triage"
        task.route.append("triage")
        journal.claim(task, lease=-1)
        recovered = journal.recover_expired()

        self.assertEqual([t.id for t in recovered], [task.id])
        self.assertEqual(journal.get(task.id).status, TaskStatus.QUEUED)
        self.assertEqual(journal.get(task.id).route, [])  # this is a retry, not a visited step

        await self.fixture.settle()
        self.assertEqual(journal.get(task.id).status, TaskStatus.DONE)


class TestLoopGuards(DispatcherCase):
    async def test_hop_limit_stops_a_long_chain(self):
        # A chain with no repeats: only the hop counter should trip.
        self.build(
            colony={"max_hops": 2},
            slot=[
                slot("a", "route_next", entry=True, next="b", can_route_to=["b"]),
                slot("b", "route_next", next="c", can_route_to=["c"]),
                slot("c", "route_next", next="d", can_route_to=["d"]),
                slot("d", "done"),
            ],
        )
        task = await self.run_one()

        self.assertEqual(task.status, TaskStatus.LOOPED)
        self.assertEqual(task.hops, 2)
        self.assertIn("hop limit", task.last_error)
        self.assertEqual(workers.calls["d"], 0)  # never reached the last slot

    async def test_a_worker_does_not_get_the_same_task_twice(self):
        self.build(
            slot=[
                slot("a", "route_next", entry=True, next="b", can_route_to=["b"]),
                slot("b", "route_back", next="a", can_route_to=["a"]),
            ],
        )
        task = await self.run_one()

        self.assertEqual(task.status, TaskStatus.LOOPED)
        self.assertIn("revisited", task.last_error)
        self.assertEqual(workers.calls["a"], 1)

    async def test_routing_outside_the_allowed_list_is_rejected(self):
        self.build(
            slot=[
                slot("a", "route_next", entry=True, next="b", can_route_to=["c"]),
                slot("b", "done"),
                slot("c", "done"),
            ],
        )
        task = await self.run_one()

        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertIn("isn't allowed to hand off", task.last_error)


class TestOutputs(DispatcherCase):
    async def test_several_outputs_each_get_the_task(self):
        self.build(
            slot=[slot("triage", "done")],
            output=[
                {"name": "log", "handler": "tests.workers:done"},
                {"name": "archive", "handler": "tests.workers:done"},
            ],
        )
        task = await self.run_one()

        self.assertEqual(task.status, TaskStatus.DONE)
        self.assertEqual(task.route, ["triage", "log", "archive"])

    async def test_output_filter_selects_by_route(self):
        self.build(
            slot=[slot("triage", "done")],
            output=[
                {"name": "deep-only", "handler": "tests.workers:done",
                 "when": {"route_contains": ["deep_dive"]}},
                {"name": "general", "handler": "tests.workers:done"},
            ],
        )
        task = await self.run_one()

        self.assertEqual(task.route, ["triage", "general"])


class TestCapacity(DispatcherCase):
    async def test_slot_does_not_take_more_than_max_instances(self):
        self.build(slot=[slot("triage", "hang", timeout=5, max_instances=2)])
        colony = self.fixture.colony
        for i in range(5):
            colony.submit({"url": f"https://{i}"})

        await colony.dispatcher.tick()
        self.assertEqual(len(colony.journal.in_progress()), 2)
        self.assertEqual(colony.journal.status_counts()["queued"], 3)

        for job in list(colony.dispatcher._inflight):
            job.cancel()
        await colony.dispatcher.drain()

    async def test_instances_are_released_after_every_outcome(self):
        # Every outcome must give the slot its capacity back, or the colony
        # quietly degrades to zero free instances.
        for handler in ("done", "fail", "refuse", "garbage"):
            with self.subTest(handler=handler):
                self.build(slot=[slot("triage", handler, max_attempts=1, max_instances=2)])
                await self.run_one()
                runtime = self.fixture.colony.slots["triage"]
                self.assertEqual([i.busy for i in runtime.instances], [False, False])
                self.fixture.close()
                self.fixture = None

    async def test_cancelled_job_does_not_eat_the_instance(self):
        self.build(slot=[slot("triage", "hang", timeout=5, max_instances=1)])
        colony = self.fixture.colony
        colony.submit({"url": "https://a"})
        await colony.dispatcher.tick()

        for job in list(colony.dispatcher._inflight):
            job.cancel()
        await colony.dispatcher.drain()

        self.assertEqual([i.busy for i in colony.slots["triage"].instances], [False])

    async def test_paused_colony_takes_nothing_new(self):
        self.build(slot=[slot("triage", "done")])
        colony = self.fixture.colony
        colony.dispatcher.paused = True
        colony.submit({"url": "https://a"})

        await colony.dispatcher.tick()
        self.assertEqual(colony.journal.status_counts().get("queued"), 1)

        colony.dispatcher.paused = False
        await self.fixture.settle()
        self.assertEqual(colony.journal.status_counts().get("done"), 1)


if __name__ == "__main__":
    unittest.main()

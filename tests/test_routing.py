"""Picking a slot when nobody named one — phase 2's remaining piece.

Two things are being checked here, and the second matters as much as the
first: that a configured router turns a refusal into a handoff, and that a
colony *without* one behaves exactly as it did before the router existed.
A colony with no key must still run.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from grokklings import routing
from grokklings.config import ConfigError, parse
from grokklings.model import SlotSpec, Task, TaskStatus
from tests import workers
from tests.helpers import ColonyFixture, slot

SCRIPTED = "tests.workers:ScriptedRouter"


class RoutingCase(unittest.IsolatedAsyncioTestCase):
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


class TestRefusalIsRehomed(RoutingCase):
    async def test_a_router_turns_not_mine_into_a_handoff(self):
        self.build(
            slot=[slot("triage", "refuse", entry=True), slot("second", "done")],
            output=[{"name": "output", "handler": "tests.workers:done"}],
            routing={"router": SCRIPTED, "pick": "second"},
        )
        task = await self.run_one()

        self.assertEqual(task.status, TaskStatus.DONE)
        self.assertEqual(task.route, ["triage", "second", "output"])

    async def test_without_a_router_a_refusal_still_lands_on_the_pile(self):
        self.build(slot=[slot("triage", "refuse", entry=True), slot("second", "done")])
        task = await self.run_one()

        self.assertEqual(task.status, TaskStatus.UNPLACED)
        self.assertIn("not my part of it", task.last_error)

    async def test_a_router_that_picks_nobody_leaves_the_task_on_the_pile(self):
        self.build(
            slot=[slot("triage", "refuse", entry=True), slot("second", "done")],
            routing={"router": SCRIPTED, "pick": "none"},
        )
        task = await self.run_one()

        self.assertEqual(task.status, TaskStatus.UNPLACED)
        self.assertIn("not my part of it", task.last_error)

    async def test_a_router_that_throws_falls_back_instead_of_stalling(self):
        """A router is user code. If it throws, the task must reach a
        terminal status, not sit in progress waiting out its lease."""
        self.build(
            slot=[slot("triage", "refuse", entry=True), slot("second", "done")],
            routing={"router": SCRIPTED, "pick": "boom"},
        )
        with self.assertLogs("grokklings.dispatcher", level="WARNING"):
            task = await self.run_one()

        self.assertEqual(task.status, TaskStatus.UNPLACED)
        types = [e["type"] for e in self.fixture.colony.journal.events(task_id=task.id)]
        self.assertIn("slot_pick_failed", types)

    async def test_the_refusing_slot_is_not_offered_back_to_the_router(self):
        self.build(
            slot=[slot("triage", "refuse", entry=True), slot("second", "done")],
            output=[{"name": "output", "handler": "tests.workers:done"}],
            routing={"router": SCRIPTED, "pick": "second"},
        )
        await self.run_one()

        offered = workers.routers_used[0].seen[0]
        self.assertNotIn("triage", offered)      # it just refused
        self.assertNotIn("output", offered)      # an output isn't a destination for a refusal
        self.assertIn("second", offered)


class TestEntryPicking(RoutingCase):
    async def test_several_entry_slots_are_chosen_between(self):
        self.build(
            slot=[
                slot("images", "done", entry=True),
                slot("links", "done", entry=True),
            ],
            output=[{"name": "output", "handler": "tests.workers:done"}],
            routing={"router": SCRIPTED, "pick": "links"},
        )
        task = await self.run_one()

        self.assertEqual(task.status, TaskStatus.DONE)
        self.assertEqual(task.route, ["links", "output"])

    async def test_a_single_entry_slot_never_costs_a_router_call(self):
        self.build(
            slot=[slot("triage", "done", entry=True), slot("second", "done")],
            output=[{"name": "output", "handler": "tests.workers:done"}],
            routing={"router": SCRIPTED, "pick": "second"},
        )
        task = await self.run_one()

        self.assertEqual(task.route, ["triage", "output"])
        self.assertEqual(workers.routers_used, [])  # nothing to decide, nobody asked

    async def test_an_unpicked_entry_leaves_the_task_on_the_pile(self):
        self.build(
            slot=[slot("images", "done", entry=True), slot("links", "done", entry=True)],
            routing={"router": SCRIPTED, "pick": "none"},
        )
        task = await self.run_one()

        self.assertEqual(task.status, TaskStatus.UNPLACED)


class TestConfig(unittest.TestCase):
    def raw(self, **extra: Any) -> dict[str, Any]:
        base = {
            "colony": {"db": ":memory:"},
            "slot": [
                {"name": "images", "handler": "tests.workers:done", "entry": True},
                {"name": "links", "handler": "tests.workers:done", "entry": True},
            ],
        }
        base.update(extra)
        return base

    def test_several_entry_slots_need_a_router_to_choose_between_them(self):
        with self.assertRaisesRegex(ConfigError, "no \\[routing\\] section"):
            parse(self.raw())

    def test_several_entry_slots_are_allowed_once_a_router_is_configured(self):
        config = parse(self.raw(routing={"router": SCRIPTED}))
        self.assertEqual(sorted(config.entries), ["images", "links"])

    def test_a_single_entry_slot_still_needs_no_routing_section(self):
        raw = self.raw()
        raw["slot"][1]["entry"] = False
        config = parse(raw)
        self.assertEqual(config.entry, "images")
        self.assertEqual(config.entries, ["images"])


class TestGrokRouter(unittest.IsolatedAsyncioTestCase):
    """The router's own request, on a fake client — same reasoning as the
    Grok worker's tests: no key here, but the request shape is checkable."""

    def slots(self) -> list[SlotSpec]:
        return [
            SlotSpec(name="images", description="anything with a picture in it"),
            SlotSpec(name="links", description="bare links to other pages"),
        ]

    def router(self, content: str) -> tuple[routing.GrokRouter, dict[str, Any]]:
        captured: dict[str, Any] = {}

        async def create(**kwargs: Any) -> Any:
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

        client = SimpleNamespace(
            with_options=lambda timeout: SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=create))
            )
        )
        router = routing.GrokRouter()
        router._client = client
        return router, captured

    async def test_the_request_carries_the_schema_and_the_slot_descriptions(self):
        router, captured = self.router('{"slot": "links", "reason": "it is a bare link"}')
        picked = await router.pick(Task(id="1", source="manual", payload={"url": "u"}), self.slots())

        self.assertEqual(picked, "links")
        fmt = captured["response_format"]
        self.assertEqual(fmt["json_schema"]["schema"], routing.PICK_SCHEMA)
        self.assertTrue(fmt["json_schema"]["strict"])
        prompt = captured["messages"][1]["content"]
        self.assertIn("anything with a picture in it", prompt)
        self.assertIn("bare links to other pages", prompt)

    async def test_a_slot_outside_the_list_is_refused(self):
        router, _ = self.router('{"slot": "somewhere_else", "reason": "made it up"}')
        picked = await router.pick(Task(id="1", source="manual", payload={}), self.slots())
        self.assertIsNone(picked)

    async def test_an_honest_none_comes_back_as_none(self):
        router, _ = self.router('{"slot": null, "reason": "none of them fit"}')
        picked = await router.pick(Task(id="1", source="manual", payload={}), self.slots())
        self.assertIsNone(picked)

    async def test_a_failed_call_is_not_an_exception_for_the_dispatcher(self):
        async def boom(**kwargs: Any) -> Any:
            raise RuntimeError("the API is down")

        router = routing.GrokRouter()
        router._client = SimpleNamespace(
            with_options=lambda timeout: SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=boom))
            )
        )
        with self.assertLogs("grokklings.routing", level="WARNING"):
            picked = await router.pick(Task(id="1", source="manual", payload={}), self.slots())
        self.assertIsNone(picked)

    async def test_one_candidate_is_not_worth_a_call(self):
        router, captured = self.router('{"slot": "images", "reason": "unused"}')
        picked = await router.pick(Task(id="1", source="manual", payload={}), self.slots()[:1])

        self.assertEqual(picked, "images")
        self.assertEqual(captured, {})


if __name__ == "__main__":
    unittest.main()

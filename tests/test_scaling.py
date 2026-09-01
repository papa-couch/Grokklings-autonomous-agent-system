"""Spawning and collapsing clones.

Spawning is the easy half. The tests that matter are the ones about *not*
spawning: a rule that reacts to the instantaneous queue depth churns
instances on every burst and never helps. So most of what's guarded here is
restraint — pressure has to hold, idleness has to hold longer, and the
ceiling and the floor are both real.
"""

from __future__ import annotations

import unittest
from typing import Any

from grokklings import scaling
from grokklings.config import parse
from grokklings.model import SlotSpec
from tests.helpers import ColonyFixture, slot


class FakeJournal:
    def __init__(self, depth: dict[str, int] | None = None) -> None:
        self.depth = depth or {}
        self.records: list[tuple[str, str | None, dict[str, Any]]] = []

    def queue_depth(self) -> dict[str, int]:
        return dict(self.depth)

    def record(self, type: str, task_id: str | None = None, slot: str | None = None, **data: Any) -> int:
        self.records.append((type, slot, data))
        return len(self.records)


class FakeInstance:
    def __init__(self, name: str, busy: bool = False) -> None:
        self.id = name
        self.busy = busy


class FakeRuntime:
    """A slot whose instance list can be inspected without a real handler."""

    def __init__(self, name: str, count: int = 1, max_instances: int = 5,
                 busy: int = 0, terminal: bool = False) -> None:
        self.spec = SlotSpec(name=name, max_instances=max_instances, terminal=terminal)
        self.instances = [FakeInstance(f"{name}#{i}", busy=i < busy) for i in range(count)]

    @property
    def load(self) -> int:
        return sum(1 for i in self.instances if i.busy)

    def free_instance(self):
        return next((i for i in self.instances if not i.busy), None)

    def add_instance(self):
        inst = FakeInstance(f"{self.spec.name}#new{len(self.instances)}")
        self.instances.append(inst)
        return inst

    def remove_instance(self, inst) -> bool:
        if inst.busy or len(self.instances) <= 1:
            return False
        self.instances.remove(inst)
        return True


class ScalingCase(unittest.TestCase):
    def scaler(self, depth: dict[str, int], entry: str | None = None, **policy: Any) -> tuple:
        journal = FakeJournal(depth)
        return scaling.Scaler(scaling.Policy(**policy), journal, entry), journal


class TestGrowing(ScalingCase):
    def test_pressure_has_to_hold_before_a_clone_appears(self):
        """The whole point: a burst that passes isn't a reason to spawn."""
        scaler, _ = self.scaler({"triage": 20}, grow_after=10.0)
        slots = {"triage": FakeRuntime("triage", count=1)}

        self.assertEqual(scaler.tick(slots, now=0.0), [])      # noticed
        self.assertEqual(scaler.tick(slots, now=5.0), [])      # still waiting
        self.assertEqual(len(slots["triage"].instances), 1)

        self.assertEqual(scaler.tick(slots, now=10.0), [("triage", "spawned")])
        self.assertEqual(len(slots["triage"].instances), 2)

    def test_a_burst_that_clears_spawns_nothing(self):
        journal = FakeJournal({"triage": 20})
        scaler = scaling.Scaler(scaling.Policy(grow_after=10.0), journal)
        slots = {"triage": FakeRuntime("triage", count=1)}

        scaler.tick(slots, now=0.0)
        journal.depth = {"triage": 0}          # the burst passed
        scaler.tick(slots, now=5.0)
        journal.depth = {"triage": 20}         # and comes back
        scaler.tick(slots, now=6.0)

        # The clock restarted, so 10s from the *first* sighting isn't enough.
        self.assertEqual(scaler.tick(slots, now=11.0), [])
        self.assertEqual(len(slots["triage"].instances), 1)

    def test_it_grows_one_at_a_time_not_to_whatever_the_backlog_suggests(self):
        scaler, _ = self.scaler({"triage": 500}, grow_after=0.0)
        slots = {"triage": FakeRuntime("triage", count=1, max_instances=10)}

        scaler.tick(slots, now=0.0)
        self.assertEqual(len(slots["triage"].instances), 2)

    def test_max_instances_is_the_ceiling(self):
        scaler, _ = self.scaler({"triage": 500}, grow_after=0.0)
        slots = {"triage": FakeRuntime("triage", count=3, max_instances=3)}

        self.assertEqual(scaler.tick(slots, now=0.0), [])
        self.assertEqual(len(slots["triage"].instances), 3)

    def test_a_backlog_the_current_instances_can_cover_is_not_pressure(self):
        # 3 waiting, 1 instance, 3 per instance — exactly covered, not over.
        scaler, _ = self.scaler({"triage": 3}, grow_after=0.0, queue_per_instance=3)
        slots = {"triage": FakeRuntime("triage", count=1)}

        self.assertEqual(scaler.tick(slots, now=0.0), [])

    def test_unrouted_work_counts_toward_the_single_entry_slot(self):
        """New tasks aren't routed yet, so without this the entry slot —
        the one actually taking the load — would never grow."""
        scaler, _ = self.scaler({scaling.UNASSIGNED: 20}, entry="triage", grow_after=0.0)
        slots = {"triage": FakeRuntime("triage", count=1)}

        self.assertEqual(scaler.tick(slots, now=0.0), [("triage", "spawned")])

    def test_with_no_single_entry_unrouted_work_is_not_attributed(self):
        """Several entry slots means the router decides later; guessing here
        would grow the wrong slot."""
        scaler, _ = self.scaler({scaling.UNASSIGNED: 20}, entry=None, grow_after=0.0)
        slots = {"images": FakeRuntime("images"), "links": FakeRuntime("links")}

        self.assertEqual(scaler.tick(slots, now=0.0), [])

    def test_an_output_is_not_scaled_from_the_queue(self):
        scaler, _ = self.scaler({"archive": 500}, grow_after=0.0)
        slots = {"archive": FakeRuntime("archive", count=1, terminal=True)}

        self.assertEqual(scaler.tick(slots, now=0.0), [])


class TestShrinking(ScalingCase):
    def idle_slots(self, count: int = 3) -> dict[str, FakeRuntime]:
        return {"triage": FakeRuntime("triage", count=count, busy=0)}

    def test_idleness_has_to_hold_much_longer_than_pressure(self):
        scaler, _ = self.scaler({}, shrink_after=60.0)
        slots = self.idle_slots()

        self.assertEqual(scaler.tick(slots, now=0.0), [])
        self.assertEqual(scaler.tick(slots, now=59.0), [])
        self.assertEqual(len(slots["triage"].instances), 3)

        self.assertEqual(scaler.tick(slots, now=60.0), [("triage", "collapsed")])
        self.assertEqual(len(slots["triage"].instances), 2)

    def test_a_slot_still_working_is_not_idle(self):
        scaler, _ = self.scaler({}, shrink_after=0.0)
        slots = {"triage": FakeRuntime("triage", count=3, busy=1)}

        self.assertEqual(scaler.tick(slots, now=0.0), [])

    def test_a_slot_with_work_waiting_is_not_idle_either(self):
        scaler, _ = self.scaler({"triage": 5}, shrink_after=0.0)
        slots = {"triage": FakeRuntime("triage", count=3, busy=0, max_instances=3)}

        self.assertEqual(scaler.tick(slots, now=0.0), [])

    def test_min_instances_is_the_floor(self):
        scaler, _ = self.scaler({}, shrink_after=0.0, min_instances=2)
        slots = self.idle_slots(count=2)

        self.assertEqual(scaler.tick(slots, now=0.0), [])
        self.assertEqual(len(slots["triage"].instances), 2)

    def test_it_gives_back_one_at_a_time(self):
        scaler, _ = self.scaler({}, shrink_after=0.0)
        slots = self.idle_slots(count=4)

        scaler.tick(slots, now=0.0)
        self.assertEqual(len(slots["triage"].instances), 3)

    def test_work_arriving_cancels_a_pending_shrink(self):
        journal = FakeJournal({})
        scaler = scaling.Scaler(scaling.Policy(shrink_after=60.0), journal)
        slots = self.idle_slots()

        scaler.tick(slots, now=0.0)
        journal.depth = {"triage": 1}     # something showed up
        scaler.tick(slots, now=30.0)
        journal.depth = {}                # and went quiet again

        self.assertEqual(scaler.tick(slots, now=61.0), [])   # the clock restarted
        self.assertEqual(len(slots["triage"].instances), 3)


class TestJournalling(ScalingCase):
    def test_a_decision_records_what_it_saw(self):
        scaler, journal = self.scaler({"triage": 50}, grow_after=0.0)
        scaler.tick({"triage": FakeRuntime("triage", count=1)}, now=0.0)

        kind, slot_name, data = journal.records[0]
        self.assertEqual((kind, slot_name), ("scaling_decision", "triage"))
        self.assertEqual(data["action"], "spawned")
        self.assertEqual(data["backlog"], 50)
        self.assertEqual(data["instances"], 2)


class TestPolicy(unittest.TestCase):
    def test_a_colony_with_no_scaling_section_gets_no_scaler(self):
        self.assertIsNone(scaling.policy_from(None))

    def test_an_empty_section_asks_for_the_defaults_it_is_not_an_absent_one(self):
        """`[scaling]` with no keys under it is a request, and silently
        doing nothing would be a trap."""
        policy = scaling.policy_from({})
        self.assertIsNotNone(policy)
        self.assertEqual(policy.min_instances, scaling.DEFAULT_MIN_INSTANCES)

    def test_the_section_is_read_into_a_policy(self):
        policy = scaling.policy_from({"min_instances": 2, "grow_after": 5})
        self.assertEqual(policy.min_instances, 2)
        self.assertEqual(policy.grow_after, 5.0)
        self.assertEqual(policy.queue_per_instance, scaling.DEFAULT_QUEUE_PER_INSTANCE)

    def test_nonsense_settings_are_refused_at_load_not_at_runtime(self):
        for bad in ({"min_instances": 0}, {"queue_per_instance": 0}, {"grow_after": -1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                scaling.policy_from(bad)


class TestColonyWiring(unittest.TestCase):
    fixture: ColonyFixture

    def tearDown(self):
        if getattr(self, "fixture", None):
            self.fixture.close()

    def build(self, **raw) -> ColonyFixture:
        self.fixture = ColonyFixture(raw)
        return self.fixture

    def test_without_scaling_a_slot_starts_at_max_instances(self):
        """Unchanged behaviour: max_instances is the whole story."""
        fixture = self.build(slot=[slot("triage", "done", max_instances=4)])

        self.assertIsNone(fixture.colony.dispatcher.scaler)
        self.assertEqual(len(fixture.colony.slots["triage"].instances), 4)

    def test_with_scaling_a_slot_starts_low_and_max_becomes_a_ceiling(self):
        fixture = self.build(
            slot=[slot("triage", "done", max_instances=4)],
            scaling={"min_instances": 1},
        )

        self.assertIsNotNone(fixture.colony.dispatcher.scaler)
        self.assertEqual(len(fixture.colony.slots["triage"].instances), 1)
        self.assertEqual(fixture.colony.slots["triage"].spec.max_instances, 4)

    def test_an_output_keeps_its_own_count_under_scaling(self):
        fixture = self.build(
            slot=[slot("triage", "done")],
            output=[{"name": "out", "handler": "tests.workers:done", "max_instances": 3}],
            scaling={"min_instances": 1},
        )
        self.assertEqual(len(fixture.colony.slots["out"].instances), 3)

    def test_the_scaler_knows_which_slot_the_unrouted_work_belongs_to(self):
        fixture = self.build(
            slot=[slot("triage", "done", entry=True), slot("second", "done")],
            scaling={},
        )
        self.assertEqual(fixture.colony.dispatcher.scaler.entry, "triage")


class TestAgainstARealColony(unittest.IsolatedAsyncioTestCase):
    """The fakes above prove the rule; this proves the wiring — a real
    dispatcher, a real journal, and tasks that actually move."""

    fixture: ColonyFixture

    async def asyncTearDown(self):
        if getattr(self, "fixture", None):
            # Drained before closing: a task still in flight would write to a
            # closed database and print a traceback that isn't a real failure.
            await self.fixture.colony.dispatcher.drain()
            self.fixture.close()

    async def test_a_burst_grows_the_entry_slot_up_to_its_ceiling(self):
        self.fixture = ColonyFixture(
            {
                "slot": [slot("triage", "done", max_instances=4, entry=True)],
                "output": [{"name": "out", "handler": "tests.workers:done"}],
                "scaling": {"grow_after": 0.0, "queue_per_instance": 2},
            }
        )
        colony = self.fixture.colony
        self.assertEqual(len(colony.slots["triage"].instances), 1)

        for i in range(30):
            colony.submit({"url": f"https://{i}"})
        # All of it is still unrouted — which is exactly the case that would
        # never scale if unassigned work weren't attributed to the entry slot.
        self.assertEqual(colony.journal.queue_depth(), {scaling.UNASSIGNED: 30})

        for _ in range(6):
            await colony.dispatcher.tick()

        self.assertEqual(len(colony.slots["triage"].instances), 4)
        actions = [
            (e["slot"], e["data"]["action"])
            for e in colony.journal.events(limit=100, type="scaling_decision")
        ]
        self.assertEqual(actions, [("triage", "spawned")] * 3)   # one at a time

    async def test_a_colony_without_scaling_never_spawns(self):
        self.fixture = ColonyFixture(
            {
                "slot": [slot("triage", "done", max_instances=4, entry=True)],
                "output": [{"name": "out", "handler": "tests.workers:done"}],
            }
        )
        colony = self.fixture.colony
        for i in range(30):
            colony.submit({"url": f"https://{i}"})
        for _ in range(6):
            await colony.dispatcher.tick()

        self.assertEqual(len(colony.slots["triage"].instances), 4)  # its static count
        self.assertEqual(colony.journal.events(limit=50, type="scaling_decision"), [])


class TestConfig(unittest.TestCase):
    def base(self, **extra: Any) -> dict[str, Any]:
        raw = {
            "colony": {"db": ":memory:"},
            "slot": [{"name": "triage", "handler": "tests.workers:done"}],
        }
        raw.update(extra)
        return raw

    def test_an_absent_section_and_an_empty_one_are_told_apart(self):
        """TOML's `[scaling]` with no keys parses to {}, and reading that as
        "no section" would silently ignore what the user asked for."""
        self.assertIsNone(parse(self.base()).scaling)
        self.assertEqual(parse(self.base(scaling={})).scaling, {})

    def test_the_same_holds_for_the_routing_section(self):
        self.assertIsNone(parse(self.base()).routing)
        self.assertEqual(parse(self.base(routing={})).routing, {})

    def test_the_scaling_section_reaches_the_config(self):
        config = parse(
            {
                "colony": {"db": ":memory:"},
                "scaling": {"min_instances": 2, "shrink_after": 90},
                "slot": [{"name": "triage", "handler": "tests.workers:done"}],
            }
        )
        self.assertEqual(config.scaling["min_instances"], 2)
        policy = scaling.policy_from(config.scaling)
        self.assertEqual(policy.shrink_after, 90.0)


if __name__ == "__main__":
    unittest.main()

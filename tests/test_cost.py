"""What the colony spent, and stopping it before it spends more.

The test that matters most is the one about a model with no price. Treating
a missing rate as zero is the natural implementation, it passes every
cheerful test, and it turns a budget into decoration: the colony reports
spending nothing while spending steadily, and the limit never fires. Most of
what's guarded here is that refusal.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from grokklings import cost
from grokklings.config import ConfigError, parse
from tests.helpers import ColonyFixture, slot

TABLE = {
    "xai/grok-4.6": {"input_cost_per_token": 2e-06, "output_cost_per_token": 6e-06},
    "claude-opus-5": {"input_cost_per_token": 5e-06, "output_cost_per_token": 2.5e-05},
    "priceless/model": {"max_input_tokens": 1000},   # in the table, but no rates
}


class FakeJournal:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.seq = 0

    def call(self, model: str | None, tokens_in: int, tokens_out: int = 0,
             slot: str = "triage", ts: float = 1000.0) -> None:
        self.seq += 1
        data = {"message": "model response received", "input_tokens": tokens_in,
                "output_tokens": tokens_out}
        if model is not None:
            data["model"] = model
        self.rows.append({"seq": self.seq, "ts": ts, "type": "worker_note",
                          "task_id": f"t{self.seq}", "slot": slot, "data": data})

    def note(self, ts: float = 1000.0) -> None:
        """A free-form worker note, not a model call."""
        self.seq += 1
        self.rows.append({"seq": self.seq, "ts": ts, "type": "worker_note",
                          "task_id": None, "slot": "triage", "data": {"message": "hello"}})

    def events(self, limit: int = 100, task_id: str | None = None, type: str | None = None):
        return [r for r in self.rows if type is None or r["type"] == type][:limit]

    def record(self, *args: Any, **kwargs: Any) -> int:
        return 0


def prices() -> cost.Prices:
    return cost.Prices(table=dict(TABLE))


class TestPrices(unittest.TestCase):
    def test_a_bare_model_name_finds_its_qualified_entry(self):
        """Configs say `grok-4.6`; the table says `xai/grok-4.6`."""
        self.assertEqual(prices().rate("grok-4.6"), (2e-06, 6e-06))

    def test_an_already_qualified_name_works_too(self):
        self.assertEqual(prices().rate("xai/grok-4.6"), (2e-06, 6e-06))

    def test_a_model_not_in_the_table_has_no_rate(self):
        self.assertIsNone(prices().rate("some-local-llama"))

    def test_an_entry_without_rates_is_not_a_rate_of_zero(self):
        """Being listed isn't being priced."""
        self.assertIsNone(prices().rate("priceless/model"))

    def test_a_missing_table_says_how_to_get_one(self):
        with self.assertRaisesRegex(cost.PricesMissing, "--refresh"):
            cost.Prices.load(Path(tempfile.mkdtemp()) / "nope.json")

    def test_a_pinned_table_round_trips(self):
        path = Path(tempfile.mkdtemp()) / "prices.json"
        path.write_text(json.dumps(TABLE))
        self.assertEqual(cost.Prices.load(path).rate("grok-4.6"), (2e-06, 6e-06))


class TestSpend(unittest.TestCase):
    def test_tokens_times_rates(self):
        journal = FakeJournal()
        journal.call("grok-4.6", 1_000_000, 200_000)

        total = cost.spend(journal, prices())
        self.assertAlmostEqual(total.total, 3.20, places=6)
        self.assertEqual(total.calls, 1)

    def test_an_unpriced_model_is_counted_as_unknown_not_as_free(self):
        """The whole point. Zero here makes a budget decorative."""
        journal = FakeJournal()
        journal.call("grok-4.6", 1_000_000, 0)     # $2.00
        journal.call("mystery-model", 9_000_000, 9_000_000)

        total = cost.spend(journal, prices())
        self.assertAlmostEqual(total.total, 2.00, places=6)
        self.assertEqual(total.unpriced_calls, 1)
        self.assertEqual(total.unpriced_models, {"mystery-model"})
        self.assertFalse(total.complete)

    def test_a_fully_priced_run_is_complete(self):
        journal = FakeJournal()
        journal.call("grok-4.6", 1000, 100)
        self.assertTrue(cost.spend(journal, prices()).complete)

    def test_free_form_notes_are_not_model_calls(self):
        journal = FakeJournal()
        journal.note()
        journal.call("grok-4.6", 1000)

        self.assertEqual(cost.spend(journal, prices()).calls, 1)

    def test_it_splits_by_model_and_by_slot(self):
        journal = FakeJournal()
        journal.call("grok-4.6", 1_000_000, 0, slot="triage")
        journal.call("claude-opus-5", 1_000_000, 0, slot="deep_dive")

        total = cost.spend(journal, prices())
        self.assertAlmostEqual(total.by_model["grok-4.6"], 2.0, places=6)
        self.assertAlmostEqual(total.by_slot["deep_dive"], 5.0, places=6)

    def test_only_what_is_new_is_added_when_asked(self):
        """A colony checking its budget every tick shouldn't re-add its history."""
        journal = FakeJournal()
        journal.call("grok-4.6", 1_000_000)
        first = cost.spend(journal, prices())
        journal.call("grok-4.6", 1_000_000)

        second = cost.spend(journal, prices(), after_seq=first.last_seq)
        self.assertEqual(second.calls, 1)
        self.assertAlmostEqual(second.total, 2.0, places=6)

    def test_a_window_excludes_what_is_older(self):
        journal = FakeJournal()
        journal.call("grok-4.6", 1_000_000, ts=100.0)
        journal.call("grok-4.6", 1_000_000, ts=5000.0)

        recent = cost.spend(journal, prices(), since=1000.0)
        self.assertEqual(recent.calls, 1)


class TestBudget(unittest.TestCase):
    def test_no_section_means_no_accounting(self):
        self.assertIsNone(cost.budget_from(None))

    def test_an_empty_section_is_a_budget_with_no_limits(self):
        budget = cost.budget_from({})
        self.assertIsNotNone(budget)
        self.assertFalse(budget)     # nothing to enforce

    def test_a_nonsense_limit_is_refused(self):
        for bad in ({"limit": 0}, {"limit": -5}, {"daily_limit": 0}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                cost.budget_from(bad)


class TestGuard(unittest.TestCase):
    def guard(self, journal: FakeJournal, **budget: Any) -> cost.Guard:
        return cost.Guard(cost.Budget(**budget), prices(), journal)

    def test_under_the_limit_nothing_happens(self):
        journal = FakeJournal()
        journal.call("grok-4.6", 1_000_000)          # $2.00
        self.assertIsNone(self.guard(journal, limit=10.0).over(now=2000.0))

    def test_over_the_limit_it_says_which_one(self):
        journal = FakeJournal()
        journal.call("grok-4.6", 1_000_000)          # $2.00
        reason = self.guard(journal, limit=1.0).over(now=2000.0)

        self.assertIsNotNone(reason)
        self.assertIn("total spend", reason)

    def test_a_daily_limit_only_counts_the_last_day(self):
        journal = FakeJournal()
        journal.call("grok-4.6", 1_000_000, ts=1000.0)         # long ago
        journal.call("grok-4.6", 1_000_000, ts=100_000.0)      # recent

        guard = self.guard(journal, daily_limit=3.0)
        # Only the recent $2.00 counts against a $3.00 daily cap.
        self.assertIsNone(guard.over(now=100_000.0))

    def test_a_daily_limit_fires_when_the_recent_spend_crosses_it(self):
        journal = FakeJournal()
        journal.call("grok-4.6", 1_000_000, ts=100_000.0)
        journal.call("grok-4.6", 1_000_000, ts=100_001.0)      # $4.00 in the window

        reason = self.guard(journal, daily_limit=3.0).over(now=100_002.0)
        self.assertIn("24h", reason)

    def test_an_incomplete_total_is_not_enforced_against(self):
        """Enforcing against a number known to be too low would pause a
        colony for the wrong reason — or, worse, fail to."""
        journal = FakeJournal()
        journal.call("mystery-model", 9_000_000, 9_000_000)

        guard = self.guard(journal, limit=0.01)
        with self.assertLogs("grokklings.cost", level="WARNING") as logs:
            self.assertIsNone(guard.over(now=2000.0))
        self.assertIn("mystery-model", "".join(logs.output))

    def test_the_running_total_accumulates_across_checks(self):
        journal = FakeJournal()
        guard = self.guard(journal, limit=100.0)

        journal.call("grok-4.6", 1_000_000)
        guard.over(now=1.0)
        journal.call("grok-4.6", 1_000_000)
        guard.over(now=2.0)

        self.assertAlmostEqual(guard.running.total, 4.0, places=6)
        self.assertEqual(guard.running.calls, 2)


class TestColonyWiring(unittest.IsolatedAsyncioTestCase):
    fixture: ColonyFixture

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="grokklings-cost-"))

    def tearDown(self):
        if getattr(self, "fixture", None):
            self.fixture.close()

    def build(self, **raw) -> ColonyFixture:
        self.fixture = ColonyFixture(raw)
        return self.fixture

    def priced_colony(self, **extra):
        """A real colony with a price table pinned beside its database.

        Built directly rather than through ColonyFixture: the fixture
        constructs the colony immediately, and the table has to exist before
        that — a limit with no prices refuses to start, which is the point.
        """
        from grokklings.colony import Colony
        from grokklings.config import parse

        (self.dir / "prices.json").write_text(json.dumps(TABLE))
        raw = {
            "colony": {"db": str(self.dir / "colony.db"), "retry_base": 0.01,
                       "poll_interval": 0.01},
            "slot": [slot("triage", "done", entry=True)],
            "output": [{"name": "out", "handler": "tests.workers:done"}],
        }
        raw.update(extra)
        colony = Colony(parse(raw))

        async def shut_down() -> None:
            # Drained, not just closed: a task still in flight would write to
            # a closed database and print a traceback that looks like a
            # product bug and isn't.
            await colony.dispatcher.drain()
            colony.close()

        self.addAsyncCleanup(shut_down)
        return colony

    def charge(self, colony, dollars_worth_of_input: int = 1_000_000) -> None:
        colony.journal.record(
            "worker_note", "t1", "triage", message="model response received",
            model="grok-4.6", input_tokens=dollars_worth_of_input, output_tokens=0,
        )

    def test_without_a_cost_section_there_is_no_guard(self):
        fixture = self.build(slot=[slot("triage", "done")])
        self.assertIsNone(fixture.colony.dispatcher.guard)

    def test_a_section_with_no_limit_reports_but_never_pauses(self):
        fixture = self.build(slot=[slot("triage", "done")], cost={})
        self.assertIsNone(fixture.colony.dispatcher.guard)

    def test_a_limit_without_a_pinned_price_table_refuses_to_start(self):
        """A limit that silently isn't enforced is worse than no limit,
        because it was asked for."""
        with self.assertRaisesRegex(ConfigError, "price table"):
            self.build(slot=[slot("triage", "done")], cost={"limit": 5.0})

    async def test_crossing_the_limit_pauses_the_colony(self):
        colony = self.priced_colony(cost={"limit": 1.0})
        self.charge(colony)                              # $2.00 against a $1.00 cap
        colony.submit({"url": "https://a"})

        launched = await colony.dispatcher.tick()

        self.assertTrue(colony.dispatcher.paused)
        self.assertEqual(launched, 0)
        events = colony.journal.events(limit=50, type="cost_limit_reached")
        self.assertEqual(len(events), 1)
        self.assertIn("total spend", events[0]["data"]["reason"])

    async def test_work_runs_normally_while_under_the_limit(self):
        colony = self.priced_colony(cost={"limit": 100.0})
        self.charge(colony)
        colony.submit({"url": "https://a"})

        launched = await colony.dispatcher.tick()
        await colony.dispatcher.drain()

        self.assertFalse(colony.dispatcher.paused)
        self.assertEqual(launched, 1)

    async def test_an_unpriced_model_does_not_trip_the_limit_but_is_said_out_loud(self):
        """Nothing is enforced against a total known to be incomplete — and
        the colony says so rather than looking healthy."""
        colony = self.priced_colony(cost={"limit": 0.01})
        colony.journal.record(
            "worker_note", "t1", "triage", message="model response received",
            model="mystery-model", input_tokens=9_000_000, output_tokens=9_000_000,
        )
        colony.submit({"url": "https://a"})

        with self.assertLogs("grokklings.cost", level="WARNING"):
            await colony.dispatcher.tick()

        self.assertFalse(colony.dispatcher.paused)


class TestConfig(unittest.TestCase):
    def test_an_absent_cost_section_is_told_from_an_empty_one(self):
        base = {
            "colony": {"db": ":memory:"},
            "slot": [{"name": "triage", "handler": "tests.workers:done"}],
        }
        self.assertIsNone(parse(base).cost)
        self.assertEqual(parse({**base, "cost": {}}).cost, {})


if __name__ == "__main__":
    unittest.main()

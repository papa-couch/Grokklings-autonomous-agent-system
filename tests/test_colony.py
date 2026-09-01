"""Assembling a colony, and keeping it up.

Two kinds of thing get pinned here. The builders decide what a colony has
before it runs, and each of them turns an absent section into something
different from an empty one — the distinction that has already been the
cause of a silently-ignored `[scaling]` block. And `source_loop` decides
what happens when an input dies at three in the morning: a source that
takes the colony down with it, or one that dies alone and unremarked, are
both worse than one that stops and says so.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from grokklings.colony import Colony, build_guard, build_router, build_source
from grokklings.config import ConfigError, parse
from grokklings.model import TaskStatus
from grokklings.sources import Source
from tests.helpers import slot


class ColonyCase(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="grokklings-colony-"))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def config(self, **raw: Any):
        base: dict[str, Any] = {
            "colony": {"db": str(self.dir / "colony.db"), "poll_interval": 0.01},
            "slot": [slot("triage", "done")],
            "output": [{"name": "output", "handler": "tests.workers:done"}],
        }
        for key, value in raw.items():
            if key == "colony":
                base["colony"].update(value)
            else:
                base[key] = value
        return parse(base)

    def colony(self, **raw: Any) -> Colony:
        it = Colony(self.config(**raw))
        self.addCleanup(it.close)
        return it


class TestBuildRouter(ColonyCase):
    """[0014](../docs/decisions/0014-grok-router.md). The absent/empty
    distinction is the whole interface here."""

    def test_no_routing_section_means_no_router(self):
        self.assertIsNone(build_router(None))

    def test_an_empty_section_asks_for_the_default(self):
        """`[routing]` with no keys under it is a request, not a blank."""
        router = build_router({})
        self.assertIsNotNone(router)
        self.assertEqual(type(router).__name__, "GrokRouter")

    def test_a_named_router_is_built(self):
        router = build_router({"router": "tests.workers:ScriptedRouter", "pick": "deep_dive"})
        self.assertEqual(router.answer, "deep_dive")

    def test_an_option_the_router_does_not_take_names_the_section(self):
        """Otherwise it's a bare TypeError from inside a factory the user
        never called."""
        with self.assertRaises(ConfigError) as caught:
            build_router({"router": "tests.workers:ScriptedRouter", "nonsense": 1})
        self.assertIn("[routing]", str(caught.exception))


class TestBuildGuard(ColonyCase):
    """[0020](../docs/decisions/0020-cost-and-limits.md)."""

    def test_no_cost_section_means_no_guard(self):
        self.assertIsNone(build_guard(self.config(), None))

    def test_a_section_with_no_limit_never_pauses_anything(self):
        """Prices without a limit is a legal setup: report, don't stop."""
        self.assertIsNone(build_guard(self.config(cost={"prices": "prices.json"}), None))

    def test_a_limit_with_no_price_table_refuses_to_start(self):
        """A limit that silently isn't enforced is worse than no limit,
        because it was asked for."""
        with self.assertRaises(ConfigError) as caught:
            build_guard(self.config(cost={"limit": 10.0}), None)
        self.assertIn("[cost]", str(caught.exception))

    def test_a_limit_with_a_price_table_gets_a_guard(self):
        (self.dir / "prices.json").write_text(
            '{"grok-4.6": {"input_cost_per_token": 1e-6, "output_cost_per_token": 5e-6}}'
        )
        guard = build_guard(self.config(cost={"limit": 10.0, "prices": "prices.json"}), None)
        self.assertIsNotNone(guard)


class TestBuildSource(ColonyCase):
    def test_a_custom_class_is_built_by_path(self):
        source = build_source({"name": "mine", "class": "tests.test_colony:QuietSource"})
        self.assertEqual(source.name, "mine")

    def test_the_named_type_picks_the_builtin(self):
        source = build_source({"name": "hook", "type": "webhook", "port": 9001})
        self.assertEqual(source.port, 9001)

    def test_a_source_that_rejects_its_options_names_the_source(self):
        """The guard reports which `[[source]]` block is wrong, rather than
        raising a bare TypeError from inside a factory the user never called."""
        with self.assertRaises(ConfigError) as caught:
            build_source({"name": "mine", "class": "tests.test_colony:StrictSource", "nope": 1})
        self.assertIn("mine", str(caught.exception))

    def test_a_typo_in_a_builtin_sources_options_is_currently_swallowed(self):
        """Documenting what happens today, not endorsing it.

        Every builtin source ends its signature in `**options`, so the guard
        above can never fire for one: `prot = 9001` instead of `port` binds
        nothing, raises nothing, and leaves the source on its default port.
        Making them strict is a behaviour change, so it is written up rather
        than slipped in — see open-questions.md."""
        source = build_source({"name": "hook", "type": "webhook", "prot": 9001})
        self.assertEqual(source.port, 8099)  # the default, silently


class QuietSource(Source):
    """Waits for the stop signal and submits nothing."""

    async def run(self, intake: Any, stop: asyncio.Event) -> None:
        await stop.wait()


class StrictSource(Source):
    """A source that names its options, the way a user's own class would."""

    def __init__(self, name: str, every: int = 60) -> None:
        super().__init__(name, every=every)
        self.every = every

    async def run(self, intake: Any, stop: asyncio.Event) -> None:
        await stop.wait()


class BrokenSource(Source):
    """Dies on its own the moment it starts."""

    async def run(self, intake: Any, stop: asyncio.Event) -> None:
        raise RuntimeError("the feed went away")


class TestAssembly(ColonyCase):
    def test_every_slot_and_output_gets_a_runtime(self):
        it = self.colony()
        self.assertEqual(sorted(it.slots), ["output", "triage"])

    def test_without_scaling_a_slot_starts_at_its_ceiling(self):
        it = self.colony(slot=[slot("triage", "done", max_instances=3)])
        self.assertEqual(len(it.slots["triage"].instances), 3)

    def test_with_scaling_a_slot_starts_low_and_grows(self):
        it = self.colony(
            slot=[slot("triage", "done", max_instances=5)],
            scaling={"min_instances": 1},
        )
        self.assertEqual(len(it.slots["triage"].instances), 1)

    def test_an_output_is_not_scaled_from_the_queue(self):
        """Outputs aren't fed by queue pressure, so the scaling floor has no
        business shrinking them."""
        it = self.colony(
            slot=[slot("triage", "done")],
            output=[{"name": "output", "handler": "tests.workers:done", "max_instances": 4}],
            scaling={"min_instances": 1},
        )
        self.assertEqual(len(it.slots["output"].instances), 4)

    def test_a_colony_without_a_routing_section_has_no_router(self):
        self.assertIsNone(self.colony().dispatcher.router)

    def test_a_colony_without_a_scaling_section_has_no_scaler(self):
        self.assertIsNone(self.colony().dispatcher.scaler)

    def test_a_colony_that_refuses_to_start_closes_its_database(self):
        """The journal opens before anything that can refuse. A [cost] limit
        with no price table is meant to stop the colony starting — and if
        that escapes without closing the journal, the handle leaks and WAL
        files are left behind, once per attempt. A supervisor retrying a bad
        config leaks steadily.

        Surfaced by CI on Python 3.13, which reports the unclosed connection
        as a ResourceWarning where 3.11 says nothing.
        """
        import sqlite3

        with self.assertRaises(ConfigError):
            Colony(self.config(cost={"limit": 5.0}))

        # If the connection were still open the journal object would be
        # unreachable, so the check is on the database itself: a second
        # process can open it and read the schema.
        db = sqlite3.connect(str(self.dir / "colony.db"))
        try:
            names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            db.close()
        self.assertIn("tasks", names)
        self.assertFalse((self.dir / "colony.db-shm").exists(),
                         "the write-ahead log is still mapped: the connection was never closed")


class TestSubmitting(ColonyCase):
    def test_a_manual_submission_works_on_a_stopped_colony(self):
        """`grokklings submit` runs against the database, not the process."""
        it = self.colony()
        admission = it.submit({"url": "https://a"})

        self.assertTrue(admission.accepted)
        self.assertEqual(it.journal.status_counts(), {"queued": 1})

    def test_a_duplicate_is_refused_and_points_at_the_first(self):
        it = self.colony(colony={"dedup_key": "url"})
        first = it.submit({"url": "https://a"})
        second = it.submit({"url": "https://a"})

        self.assertTrue(first.accepted)
        self.assertFalse(second.accepted)
        self.assertEqual(second.duplicate_of, first.task.id)


class TestStatus(ColonyCase):
    def test_status_reports_each_slot(self):
        it = self.colony(slot=[slot("triage", "done", max_instances=2)])
        status = it.status()

        triage = next(s for s in status["slots"] if s["name"] == "triage")
        self.assertEqual(triage["instances"], 2)
        self.assertEqual(triage["busy"], 0)
        self.assertFalse(triage["terminal"])

    def test_an_output_is_marked_terminal(self):
        status = self.colony().status()
        output = next(s for s in status["slots"] if s["name"] == "output")
        self.assertTrue(output["terminal"])

    def test_queued_work_shows_up(self):
        it = self.colony()
        it.submit({"url": "https://a"})
        self.assertEqual(it.status()["tasks"], {"queued": 1})

    def test_a_fresh_colony_is_not_paused(self):
        self.assertFalse(self.colony().status()["paused"])

    def test_unplaced_work_is_counted_not_listed(self):
        """The number is the signal the brain reads; the pile itself can be
        thousands of rows."""
        it = self.colony()
        it.submit({"url": "https://a"})
        task = it.journal.peek()[0]
        it.journal.finish(task, TaskStatus.UNPLACED)

        self.assertEqual(it.status()["unplaced"], 1)


class TestSourceLoop(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="grokklings-colony-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        config = parse(
            {
                "colony": {"db": str(self.dir / "colony.db"), "poll_interval": 0.01},
                "slot": [slot("triage", "done")],
                "output": [{"name": "output", "handler": "tests.workers:done"}],
            }
        )
        self.colony = Colony(config)
        self.addCleanup(self.colony.close)

    async def test_a_source_that_dies_does_not_take_the_colony_with_it(self):
        await self.colony.source_loop(BrokenSource(name="feed"), asyncio.Event())
        # Returned rather than raised: the dispatcher and the other sources
        # are still running.

    async def test_a_dead_source_is_written_down_and_alerted(self):
        """Otherwise the colony goes quiet at 3am and looks idle."""
        await self.colony.source_loop(BrokenSource(name="feed"), asyncio.Event())

        stopped = self.colony.journal.events(type="source_stopped")
        self.assertEqual(stopped[0]["data"]["source"], "feed")
        self.assertIn("the feed went away", stopped[0]["data"]["error"])

        alerts = self.colony.journal.events(type="alert")
        self.assertEqual(alerts[0]["data"]["kind"], "source_down")

    async def test_a_cancelled_source_is_a_clean_shutdown_not_a_failure(self):
        """Stopping the colony must not fill the journal with alerts.

        Guarded twice over, which is worth knowing when reading the loop:
        `CancelledError` is a `BaseException`, so the `except Exception`
        below would miss it anyway, and the explicit re-raise above catches
        it first regardless. Removing either one alone changes nothing;
        this test goes red when both go."""
        stop = asyncio.Event()
        job = asyncio.create_task(self.colony.source_loop(QuietSource(name="quiet"), stop))
        await asyncio.sleep(0.01)
        job.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await job
        self.assertEqual(self.colony.journal.events(type="alert"), [])


class TestRunning(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="grokklings-colony-"))
        self.addCleanup(shutil.rmtree, self.dir, True)

    async def test_the_stop_signal_brings_everything_down(self):
        config = parse(
            {
                "colony": {"db": str(self.dir / "colony.db"), "poll_interval": 0.01},
                "source": [{"name": "quiet", "class": "tests.test_colony:QuietSource"}],
                "slot": [slot("triage", "done")],
                "output": [{"name": "output", "handler": "tests.workers:done"}],
            }
        )
        it = Colony(config)
        self.addCleanup(it.close)
        stop = asyncio.Event()

        job = asyncio.create_task(it.run(stop))
        it.submit({"url": "https://a"})

        # Wait for the work, not for a slice of wall clock. A fixed sleep
        # here asks "is a busy machine finished yet?" and accepts "no" as a
        # pass; on a loaded CI runner this test failed exactly that way,
        # with `status_counts()` still empty after 0.15s. Polling asks the
        # question the test means to ask, and the deadline is generous
        # enough that going red means the work genuinely never happened.
        deadline = time.monotonic() + 10
        while it.journal.status_counts().get("done") != 1:
            if time.monotonic() > deadline:
                self.fail(f"the task never finished: {it.journal.status_counts()}")
            await asyncio.sleep(0.01)

        stop.set()
        await asyncio.wait_for(job, timeout=5)

        # The work went through before the shutdown, and nothing is left running.
        self.assertEqual(it.journal.status_counts().get("done"), 1)
        self.assertTrue(job.done())


if __name__ == "__main__":
    unittest.main()

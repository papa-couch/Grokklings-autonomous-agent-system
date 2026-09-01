"""Scoring finished work against criteria the user wrote.

The cases that matter most here are the ones where a plausible
implementation would quietly do the wrong thing: recording "we couldn't
tell" as a zero, letting a crossed red line average away behind a high
score, or scoring the same task twice and skewing everything built on top.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from grokklings import evaluate
from grokklings.config import parse
from grokklings.model import SlotSpec, Task
from tests.helpers import ColonyFixture, slot

CRITERIA = evaluate.Criteria(
    looking_for="new tools for sorting a task stream",
    counts_as="a verdict with a reason and a confidence level",
    never="inventing facts that weren't in the source",
)


def client_returning(content: str, captured: dict[str, Any] | None = None) -> Any:
    async def create(**kwargs: Any) -> Any:
        if captured is not None:
            captured.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    return SimpleNamespace(
        with_options=lambda timeout: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
    )


def evaluator_returning(content: str, captured: dict[str, Any] | None = None) -> evaluate.GrokEvaluator:
    ev = evaluate.GrokEvaluator()
    ev._client = client_returning(content, captured)
    return ev


class TestCriteria(unittest.TestCase):
    def test_a_colony_with_nothing_written_down_is_not_scored(self):
        self.assertFalse(evaluate.Criteria())
        self.assertFalse(evaluate.criteria_from({}))

    def test_any_one_of_the_three_questions_is_enough_to_start(self):
        self.assertTrue(evaluate.criteria_from({"never": "don't make things up"}))

    def test_the_prompt_carries_what_the_user_actually_wrote(self):
        text = CRITERIA.as_prompt()
        self.assertIn("new tools for sorting a task stream", text)
        self.assertIn("a verdict with a reason", text)
        self.assertIn("inventing facts", text)


class TestScoring(unittest.IsolatedAsyncioTestCase):
    def task(self) -> Task:
        task = Task(id="t1", source="manual", payload={"url": "https://a"})
        task.route.append("triage")
        task.add("triage", "triage#0", {"verdict": "worth a look", "confidence": 0.8})
        return task

    async def test_a_score_comes_back_parsed(self):
        ev = evaluator_returning(
            '{"score": 0.9, "met": true, "feedback": "reason and confidence both present", '
            '"violations": []}'
        )
        got = await ev.score(self.task(), CRITERIA)

        self.assertEqual(got.score, 0.9)
        self.assertTrue(got.met)
        self.assertTrue(got.clean)

    async def test_a_red_line_is_not_averaged_into_the_score(self):
        """An excellent result that did something forbidden is still flagged."""
        ev = evaluator_returning(
            '{"score": 0.95, "met": true, "feedback": "thorough", '
            '"violations": ["invented a funding round that is not in the source"]}'
        )
        got = await ev.score(self.task(), CRITERIA)

        self.assertEqual(got.score, 0.95)   # the score stays high
        self.assertTrue(got.met)            # and it met the bar
        self.assertFalse(got.clean)         # but it is not clean
        self.assertEqual(len(got.violations), 1)

    async def test_a_failed_call_is_not_a_zero(self):
        """"Couldn't tell" and "was bad" are different answers."""
        async def boom(**kwargs: Any) -> Any:
            raise RuntimeError("the API is down")

        ev = evaluate.GrokEvaluator()
        ev._client = SimpleNamespace(
            with_options=lambda timeout: SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=boom))
            )
        )
        with self.assertLogs("grokklings.evaluate", level="WARNING"):
            self.assertIsNone(await ev.score(self.task(), CRITERIA))

    async def test_a_response_with_no_usable_score_is_not_a_zero_either(self):
        ev = evaluator_returning('{"met": true, "feedback": "good", "violations": []}')
        with self.assertLogs("grokklings.evaluate", level="WARNING"):
            self.assertIsNone(await ev.score(self.task(), CRITERIA))

    async def test_a_score_outside_the_range_is_clamped(self):
        ev = evaluator_returning('{"score": 4.2, "met": true, "feedback": "x", "violations": []}')
        got = await ev.score(self.task(), CRITERIA)
        self.assertEqual(got.score, 1.0)

    async def test_nothing_is_scored_without_criteria(self):
        ev = evaluator_returning('{"score": 1, "met": true, "feedback": "x", "violations": []}')
        self.assertIsNone(await ev.score(self.task(), evaluate.Criteria()))

    async def test_the_request_carries_the_schema_and_the_slots_own_criteria(self):
        captured: dict[str, Any] = {}
        ev = evaluator_returning(
            '{"score": 0.5, "met": false, "feedback": "x", "violations": []}', captured
        )
        spec = SlotSpec(name="triage", success="a one-line verdict, no hedging")
        await ev.score(self.task(), CRITERIA, spec)

        fmt = captured["response_format"]
        self.assertEqual(fmt["json_schema"]["schema"], evaluate.ASSESSMENT_SCHEMA)
        self.assertTrue(fmt["json_schema"]["strict"])
        prompt = captured["messages"][1]["content"]
        self.assertIn("a one-line verdict, no hedging", prompt)
        self.assertIn("worth a look", prompt)     # what the colony produced
        self.assertIn("inventing facts", prompt)  # the red line


class StubEvaluator:
    """Scores everything the same, and counts how often it was asked."""

    def __init__(self, score: float = 0.8, met: bool = True, violations: list[str] | None = None) -> None:
        self.value = score
        self.met = met
        self.violations = violations or []
        self.seen: list[str] = []

    async def score(self, task, criteria, slot=None):
        self.seen.append(task.id)
        return evaluate.Assessment(
            task_id=task.id, score=self.value, met=self.met,
            feedback="stub", violations=list(self.violations),
        )


class TestScoringPass(unittest.IsolatedAsyncioTestCase):
    fixture: ColonyFixture

    def tearDown(self):
        if getattr(self, "fixture", None):
            self.fixture.close()

    async def finished_colony(self) -> ColonyFixture:
        self.fixture = ColonyFixture(
            {
                "slot": [slot("triage", "done")],
                "output": [{"name": "output", "handler": "tests.workers:done"}],
            }
        )
        self.fixture.colony.submit({"url": "https://a"})
        self.fixture.colony.submit({"url": "https://b"})
        await self.fixture.settle()
        return self.fixture

    async def test_each_finished_task_is_scored_and_written_to_the_journal(self):
        fixture = await self.finished_colony()
        stub = StubEvaluator()
        done = await evaluate.score_finished(fixture.colony.journal, CRITERIA, stub)

        self.assertEqual(len(done), 2)
        events = fixture.colony.journal.events(limit=100, type="task_scored")
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["data"]["score"], 0.8)

    async def test_a_second_pass_does_not_score_the_same_task_again(self):
        fixture = await self.finished_colony()
        stub = StubEvaluator()
        await evaluate.score_finished(fixture.colony.journal, CRITERIA, stub)
        again = await evaluate.score_finished(fixture.colony.journal, CRITERIA, stub)

        self.assertEqual(again, [])
        self.assertEqual(len(stub.seen), 2)  # not 4

    async def test_a_long_running_colony_still_does_not_rescore(self):
        """The guarantee above held only while the colony was young.

        The already-scored set used to be read from the newest 10,000
        `task_scored` events. Once that many newer scores arrive, an older
        task's score falls out of the window, the task reads as unscored,
        and it is scored again — a wasted model call, and a duplicate the
        brain's payoff comparison and the calibration sample both average
        in. Self-amplifying, too: each re-score adds an event that pushes
        the window further along.
        """
        fixture = await self.finished_colony()
        stub = StubEvaluator()
        await evaluate.score_finished(fixture.colony.journal, CRITERIA, stub)

        for i in range(10_000):
            fixture.colony.journal.record("task_scored", f"other{i}", "triage", score=0.5, met=True)

        again = await evaluate.score_finished(fixture.colony.journal, CRITERIA, stub)
        self.assertEqual(again, [])
        self.assertEqual(len(stub.seen), 2)  # not 4

    async def test_rescore_asks_again_on_purpose(self):
        fixture = await self.finished_colony()
        stub = StubEvaluator()
        await evaluate.score_finished(fixture.colony.journal, CRITERIA, stub)
        again = await evaluate.score_finished(fixture.colony.journal, CRITERIA, stub, rescore=True)
        self.assertEqual(len(again), 2)

    async def test_an_unscorable_task_is_left_for_the_next_pass(self):
        """Nothing is recorded when scoring fails, so it isn't silently skipped forever."""
        fixture = await self.finished_colony()

        class Failing:
            async def score(self, task, criteria, slot=None):
                return None

        done = await evaluate.score_finished(fixture.colony.journal, CRITERIA, Failing())
        self.assertEqual(done, [])
        self.assertEqual(fixture.colony.journal.events(limit=100, type="task_scored"), [])

        # The next pass, with a working evaluator, still finds them.
        done = await evaluate.score_finished(fixture.colony.journal, CRITERIA, StubEvaluator())
        self.assertEqual(len(done), 2)

    async def test_without_criteria_nothing_is_scored_at_all(self):
        fixture = await self.finished_colony()
        stub = StubEvaluator()
        done = await evaluate.score_finished(fixture.colony.journal, evaluate.Criteria(), stub)

        self.assertEqual(done, [])
        self.assertEqual(stub.seen, [])


class TestConfig(unittest.TestCase):
    def test_the_success_section_reaches_the_criteria(self):
        config = parse(
            {
                "colony": {"db": ":memory:"},
                "success": {"looking_for": "signal", "never": "made-up facts"},
                "slot": [{"name": "triage", "handler": "tests.workers:done"}],
            }
        )
        criteria = evaluate.criteria_from(config.success)
        self.assertEqual(criteria.looking_for, "signal")
        self.assertEqual(criteria.never, "made-up facts")

    def test_a_slot_carries_its_own_success_criteria(self):
        config = parse(
            {
                "colony": {"db": ":memory:"},
                "slot": [
                    {
                        "name": "triage",
                        "handler": "tests.workers:done",
                        "success": "a verdict in one line",
                    }
                ],
            }
        )
        self.assertEqual(config.slots[0].success, "a verdict in one line")
        # It's criteria for the judge, not instructions for the worker.
        self.assertNotIn("success", config.slots[0].options)


if __name__ == "__main__":
    unittest.main()

"""Optimizing a slot's instructions with GEPA.

The last test runs the real GEPA engine. That's deliberate: the first
version of this adapter passed every test written against the documented
interface and still optimized nothing, because GEPA reads an attribute the
docs call optional. Only driving the real engine caught it.
"""

from __future__ import annotations

import unittest
from typing import Any

from grokklings import optimize
from grokklings.evaluate import Assessment, Criteria
from grokklings.model import SlotSpec, Task

try:
    import gepa  # noqa: F401

    HAS_GEPA = True
except ModuleNotFoundError:  # pragma: no cover - depends on the environment
    HAS_GEPA = False

CRITERIA = Criteria(counts_as="a verdict with a reason")
SPEC = SlotSpec(name="triage", description="screening", context="Summarise the page.")


def tasks(n: int = 6) -> list[Task]:
    return [Task(id=f"t{i}", source="manual", payload={"url": f"https://{i}"}) for i in range(n)]


def run_ok(task: Task, context: str) -> dict[str, Any]:
    return {"answer": f"{context}|{task.payload['url']}"}


def score_flat(value: float = 0.5, **extra: Any):
    def score(task: Task, output: Any) -> Assessment:
        return Assessment(task_id=task.id, score=value, met=value >= 0.5,
                          feedback="it was fine", **extra)
    return score


@unittest.skipUnless(HAS_GEPA, "GEPA isn't installed")
class TestAdapter(unittest.TestCase):
    def adapter(self, run=run_ok, score=None) -> optimize.SlotAdapter:
        return optimize.SlotAdapter(SPEC, CRITERIA, run, score or score_flat())

    def test_scores_come_back_one_per_task(self):
        batch = self.adapter().evaluate(tasks(4), {"context": "new"}, capture_traces=True)

        self.assertEqual(len(batch.scores), 4)
        self.assertEqual(len(batch.trajectories), 4)
        self.assertEqual(batch.scores, [0.5] * 4)

    def test_the_candidate_context_is_what_actually_runs(self):
        """Not the slot's current one — that's the thing being replaced."""
        batch = self.adapter().evaluate(tasks(1), {"context": "CANDIDATE"}, capture_traces=True)
        self.assertIn("CANDIDATE", batch.outputs[0]["answer"])

    def test_traces_are_skipped_when_not_asked_for(self):
        batch = self.adapter().evaluate(tasks(2), {"context": "x"}, capture_traces=False)
        self.assertIsNone(batch.trajectories)

    def test_a_failing_task_is_a_zero_not_an_exception(self):
        """GEPA's contract: never raise for one example."""
        def explode(task: Task, context: str) -> Any:
            raise RuntimeError("the worker died")

        with self.assertLogs("grokklings.optimize", level="WARNING"):
            batch = self.adapter(run=explode).evaluate(tasks(3), {"context": "x"}, capture_traces=True)

        self.assertEqual(batch.scores, [0.0, 0.0, 0.0])
        self.assertEqual(len(batch.trajectories), 3)   # still traced, so the teacher sees why

    def test_metric_calls_are_counted_because_they_cost_money(self):
        adapter = self.adapter()
        adapter.evaluate(tasks(5), {"context": "x"})
        adapter.evaluate(tasks(3), {"context": "y"})
        self.assertEqual(adapter.metric_calls, 8)

    def test_the_reflective_dataset_carries_the_judges_feedback(self):
        adapter = self.adapter()
        batch = adapter.evaluate(tasks(2), {"context": "x"}, capture_traces=True)
        data = adapter.make_reflective_dataset({"context": "x"}, batch, ["context"])

        records = data["context"]
        self.assertEqual(len(records), 2)
        self.assertIn("Inputs", records[0])
        self.assertIn("Generated Outputs", records[0])
        self.assertEqual(records[0]["Feedback"], "it was fine")

    def test_a_crossed_red_line_reaches_the_teacher(self):
        """It's the strongest signal about what the instructions permit."""
        adapter = self.adapter(score=score_flat(0.9, violations=["invented a source"]))
        batch = adapter.evaluate(tasks(1), {"context": "x"}, capture_traces=True)
        data = adapter.make_reflective_dataset({"context": "x"}, batch, ["context"])

        self.assertIn("invented a source", data["context"][0]["Feedback"])

    def test_a_crash_and_an_unjudgeable_run_teach_different_lessons(self):
        """Both are "no assessment". One says the instructions produce
        something broken; the other says nothing about them at all."""
        def unjudgeable(task: Task, output: Any) -> None:
            return None

        adapter = self.adapter(score=unjudgeable)
        batch = adapter.evaluate(tasks(1), {"context": "x"}, capture_traces=True)
        quiet = adapter.make_reflective_dataset({"context": "x"}, batch, ["context"])
        self.assertIn("could not be judged", quiet["context"][0]["Feedback"])
        self.assertNotIn("failed", quiet["context"][0]["Feedback"])

        def explode(task: Task, context: str) -> Any:
            raise RuntimeError("the worker died")

        broken = self.adapter(run=explode)
        with self.assertLogs("grokklings.optimize", level="WARNING"):
            batch = broken.evaluate(tasks(1), {"context": "x"}, capture_traces=True)
        data = broken.make_reflective_dataset({"context": "x"}, batch, ["context"])
        self.assertIn("failed", data["context"][0]["Feedback"])
        self.assertIn("the worker died", data["context"][0]["Feedback"])


class TestGuards(unittest.TestCase):
    def test_too_few_tasks_is_refused(self):
        with self.assertRaisesRegex(ValueError, "floor"):
            optimize.optimize_context(SPEC, tasks(2), CRITERIA, run_ok, score_flat())

    def test_optimizing_against_nothing_is_refused(self):
        with self.assertRaisesRegex(ValueError, r"\[success\]"):
            optimize.optimize_context(SPEC, tasks(8), Criteria(), run_ok, score_flat())


class TestProposal(unittest.TestCase):
    def result(self, after: str = "better instructions", before_score=0.2, after_score=0.9):
        return optimize.Optimized(
            slot="triage", before="old instructions", after=after,
            score_before=before_score, score_after=after_score, metric_calls=40,
        )

    def test_the_outcome_is_a_proposal_not_a_change(self):
        """A better context that installed itself would still be the colony
        rewriting its own instructions."""
        proposal = self.result().as_proposal()

        self.assertEqual(proposal.kind, "edit_context")
        self.assertEqual(proposal.target, "triage")
        self.assertEqual(proposal.change, "better instructions")
        self.assertTrue(proposal.grounded)
        self.assertIn("0.90", proposal.rationale)

    def test_no_gain_is_not_an_improvement(self):
        self.assertFalse(self.result(after_score=0.2).improved)

    def test_an_unchanged_context_is_not_an_improvement_either(self):
        self.assertFalse(self.result(after="old instructions", after_score=0.9).improved)


@unittest.skipUnless(HAS_GEPA, "GEPA isn't installed")
class TestAgainstTheRealEngine(unittest.TestCase):
    """Drives GEPA itself. The adapter passed every unit test above while
    optimizing nothing, so this is the test that actually proves it works."""

    def test_gepa_finds_instructions_that_score_better(self):
        wanted = "cite the source"

        def run(task: Task, context: str) -> dict[str, str]:
            return {"answer": f"[{context}] {task.payload['url']}"}

        def score(task: Task, output: Any) -> Assessment:
            good = wanted in output["answer"].lower()
            return Assessment(
                task_id=task.id, score=1.0 if good else 0.2, met=good,
                feedback="Cites the source." if good else "It never says where the claim came from.",
            )

        consulted = []

        def teacher(prompt: Any) -> str:
            consulted.append(prompt)
            return "Summarise the page, and cite the source for every claim."

        result = optimize.optimize_context(
            SPEC, tasks(6), CRITERIA, run, score, budget=25, teacher=teacher
        )

        self.assertTrue(consulted, "the teacher was never asked — reflection didn't run")
        self.assertIn(wanted, result.after.lower())
        self.assertGreater(result.score_after, result.score_before)
        self.assertTrue(result.improved)

    def test_the_engine_can_reach_the_default_proposer(self):
        """Regression: GEPA reads adapter.propose_new_texts directly, so an
        adapter that merely omits it raises mid-search and silently stops
        proposing. The attribute has to exist."""
        self.assertTrue(hasattr(optimize.SlotAdapter, "propose_new_texts"))
        self.assertIsNone(optimize.SlotAdapter.propose_new_texts)


if __name__ == "__main__":
    unittest.main()

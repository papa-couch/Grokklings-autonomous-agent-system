"""Checking the judge against hand labels, via CJE.

The test that matters is the last one: plant a judge that scores high, and
see the calibration say so and by how much. Everything before it guards the
ways this check can be made meaningless without failing — a sample that
isn't random, a worksheet that shows the answer, an interval computed from
too few labels.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from grokklings import calibrate

try:
    import cje  # noqa: F401

    HAS_CJE = True
except ModuleNotFoundError:  # pragma: no cover - depends on the environment
    HAS_CJE = False


def rows(n: int, judge=lambda i: i / 100) -> list[calibrate.Row]:
    return [
        calibrate.Row(
            task_id=f"t{i}",
            slot="triage",
            payload={"url": f"https://{i}"},
            result={"verdict": "ok"},
            judge_score=judge(i),
        )
        for i in range(n)
    ]


class TestWorksheet(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="grokklings-cal-"))

    def test_the_worksheet_never_shows_the_judges_score(self):
        """Seeing it is how you end up agreeing with it."""
        path = self.dir / "sheet.jsonl"
        calibrate.write_worksheet(rows(5), path)

        for line in path.read_text().splitlines():
            entry = json.loads(line)
            self.assertNotIn("judge_score", entry)
            self.assertIsNone(entry["label"])
            self.assertIn("payload", entry)   # but enough to judge on
            self.assertIn("result", entry)

    def test_the_sample_is_random_not_the_worst_cases(self):
        """Labeling only what looks wrong calibrates against a fiction."""
        picked = calibrate.worksheet(rows(100), 20, seed=1)
        scores = [r.judge_score for r in picked]

        self.assertEqual(len(picked), 20)
        # A "worst first" sample would sit at the bottom of the range; a
        # random one spreads across it.
        self.assertGreater(max(scores) - min(scores), 0.4)

    def test_the_same_seed_draws_the_same_sample(self):
        first = [r.task_id for r in calibrate.worksheet(rows(50), 10, seed=7)]
        second = [r.task_id for r in calibrate.worksheet(rows(50), 10, seed=7)]
        self.assertEqual(first, second)

    def test_asking_for_more_than_exists_gives_everything(self):
        self.assertEqual(len(calibrate.worksheet(rows(5), 20)), 5)

    def test_blank_and_broken_labels_are_skipped_not_guessed(self):
        path = self.dir / "filled.jsonl"
        path.write_text(
            "\n".join(
                json.dumps(entry)
                for entry in [
                    {"task_id": "t1", "label": 0.8},
                    {"task_id": "t2", "label": None},      # not labeled
                    {"task_id": "t3", "label": ""},        # not labeled
                    {"task_id": "t4", "label": "nonsense"},
                    {"task_id": "t5", "label": 1.4},       # outside 0..1
                    {"task_id": "t6", "label": "0.5"},     # a number as text is fine
                ]
            )
        )
        with self.assertLogs("grokklings.calibrate", level="WARNING"):
            labels = calibrate.read_labels(path)

        self.assertEqual(labels, {"t1": 0.8, "t6": 0.5})


@unittest.skipUnless(HAS_CJE, "CJE isn't installed")
class TestCalibration(unittest.TestCase):
    def test_too_few_labels_is_refused_not_answered(self):
        """A confidence interval from four labels is still believed."""
        with self.assertRaisesRegex(ValueError, "floor"):
            calibrate.calibrate(rows(50), {f"t{i}": 0.5 for i in range(4)})

    def test_labels_for_tasks_that_are_not_there_do_not_count(self):
        with self.assertRaisesRegex(ValueError, "floor"):
            calibrate.calibrate(rows(50), {f"missing{i}": 0.5 for i in range(20)})

    def test_a_judge_that_scores_high_is_caught_and_measured(self):
        """The point of the whole exercise."""
        # The truth is i/100; the judge reports 0.2 higher than that.
        truth = {f"t{i}": i / 100 for i in range(100)}
        scored = rows(100, judge=lambda i: min(1.0, i / 100 + 0.2))

        # A random slice, judged by hand — which here means: the truth.
        sample = calibrate.worksheet(scored, 40, seed=3)
        labels = {r.task_id: truth[r.task_id] for r in sample}

        result = calibrate.calibrate(scored, labels)

        self.assertGreater(result.judge_mean, 0.65)     # what the judge claims
        self.assertLess(result.calibrated, 0.60)        # what it's worth
        self.assertGreater(result.drift, 0.1)           # and it says by how much
        self.assertEqual(result.n_labelled, 40)
        self.assertLess(result.ci[0], result.calibrated)
        self.assertGreater(result.ci[1], result.calibrated)

    def test_an_honest_judge_shows_little_drift(self):
        truth = {f"t{i}": i / 100 for i in range(100)}
        scored = rows(100, judge=lambda i: i / 100)
        sample = calibrate.worksheet(scored, 40, seed=3)

        result = calibrate.calibrate(scored, {r.task_id: truth[r.task_id] for r in sample})
        self.assertLess(abs(result.drift), 0.05)

    def test_it_says_when_the_labels_do_not_cover_the_judges_range(self):
        """Labeling only the low end can't support a claim about the whole."""
        truth = {f"t{i}": i / 100 for i in range(100)}
        scored = rows(100)
        # Deliberately not random: only the bottom third gets labeled.
        labels = {f"t{i}": truth[f"t{i}"] for i in range(30)}

        result = calibrate.calibrate(scored, labels)
        self.assertTrue(result.refused)
        self.assertGreater(result.out_of_range, 0.1)
        self.assertFalse(result.trustworthy)

    def test_a_thin_edge_is_a_note_not_a_refusal(self):
        """CJE's CAUTION means the estimate stands. Treating it as a refusal
        would cry wolf on every honest run."""
        truth = {f"t{i}": i / 100 for i in range(100)}
        scored = rows(100, judge=lambda i: min(1.0, i / 100 + 0.2))
        sample = calibrate.worksheet(scored, 50, seed=5)

        result = calibrate.calibrate(scored, {r.task_id: truth[r.task_id] for r in sample})
        self.assertFalse(result.refused)
        self.assertTrue(result.trustworthy)


if __name__ == "__main__":
    unittest.main()

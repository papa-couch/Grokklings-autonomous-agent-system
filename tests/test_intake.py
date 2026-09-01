from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from grokklings.intake import Intake, extract_key
from grokklings.journal import Journal
from grokklings.model import TaskStatus


class TestKeyExtraction(unittest.TestCase):
    def test_dotted_path(self):
        self.assertEqual(extract_key({"repo": {"url": "u"}}, "repo.url"), "u")

    def test_missing_path_means_no_dedup(self):
        self.assertIsNone(extract_key({"repo": {}}, "repo.url"))

    def test_whole_payload_is_stable_regardless_of_key_order(self):
        a = extract_key({"a": 1, "b": 2}, "@payload")
        b = extract_key({"b": 2, "a": 1}, "@payload")
        self.assertEqual(a, b)

    def test_no_spec_means_no_dedup(self):
        self.assertIsNone(extract_key({"a": 1}, None))


class TestIntake(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.journal = Journal(str(Path(self.dir.name) / "c.db"))

    def tearDown(self):
        self.journal.close()
        self.dir.cleanup()

    def test_duplicate_is_dropped_with_reference_to_first(self):
        intake = Intake(self.journal, "url")
        first = intake.submit("manual", {"url": "https://a"})
        second = intake.submit("manual", {"url": "https://a"})

        self.assertTrue(first.accepted)
        self.assertFalse(second.accepted)
        self.assertEqual(second.duplicate_of, first.task.id)
        # The duplicate is stored too: a task never disappears.
        stored = self.journal.get(second.task.id)
        self.assertEqual(stored.status, TaskStatus.DROPPED)
        self.assertEqual(stored.duplicate_of, first.task.id)

    def test_dedup_survives_restart(self):
        path = self.journal.path
        Intake(self.journal, "url").submit("manual", {"url": "https://a"})
        self.journal.close()

        reopened = Journal(path)
        try:
            again = Intake(reopened, "url").submit("manual", {"url": "https://a"})
            self.assertFalse(again.accepted)
        finally:
            reopened.close()

    def test_without_dedup_key_everything_passes(self):
        intake = Intake(self.journal, None)
        self.assertTrue(intake.submit("manual", {"url": "https://a"}).accepted)
        self.assertTrue(intake.submit("manual", {"url": "https://a"}).accepted)

    def test_arrival_is_journalled(self):
        intake = Intake(self.journal, "url")
        task = intake.submit("manual", {"url": "https://a"}).task
        types = [e["type"] for e in self.journal.events(task_id=task.id)]
        self.assertEqual(types, ["task_received", "task_queued"])


if __name__ == "__main__":
    unittest.main()

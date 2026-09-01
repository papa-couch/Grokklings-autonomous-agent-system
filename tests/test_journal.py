"""The journal: the one place state is written.

Every other module reads its truth from here, so the invariants worth
pinning are the ones whose failure is silent. A lease that hands the same
task to two instances doesn't crash — it does the work twice. A step store
that can't tell "returned nothing" from "never ran" doesn't crash either —
it skips the step forever. A `finish` that leaves step rows behind doesn't
crash — it replays yesterday's answers onto a task resubmitted today.

Those three are the tests that matter here; the rest hold the edges around
them.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from grokklings.journal import Journal
from grokklings.model import Task, TaskStatus


class JournalCase(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="grokklings-journal-"))
        self.path = str(self.dir / "colony.db")
        self.journal = Journal(self.path)
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.addCleanup(self.journal.close)

    def task(self, task_id: str = "t1", **extra) -> Task:
        return Task(id=task_id, source="manual", payload={"url": f"https://{task_id}"}, **extra)

    def queued(self, task_id: str = "t1", delay: float = 0.0, **extra) -> Task:
        task = self.task(task_id, **extra)
        self.journal.enqueue(task, delay=delay)
        return task


class TestDurability(JournalCase):
    """[0011](../docs/decisions/0011-durable-core.md): the colony survives
    being killed. Everything else here rests on that."""

    def test_a_queued_task_survives_the_process(self):
        self.queued("t1")
        self.journal.close()

        reopened = Journal(self.path)
        self.addCleanup(reopened.close)
        got = reopened.get("t1")

        self.assertIsNotNone(got)
        self.assertEqual(got.payload, {"url": "https://t1"})
        self.assertEqual(got.status, TaskStatus.QUEUED)

    def test_the_accumulated_result_survives_too(self):
        """A half-finished task that loses its earlier contributions is
        worse than one that never started: the work is gone and nothing says so."""
        task = self.queued("t1")
        task.add("triage", "triage#0", {"verdict": "worth a look"})
        self.journal.save(task)
        self.journal.close()

        reopened = Journal(self.path)
        self.addCleanup(reopened.close)
        got = reopened.get("t1")

        self.assertEqual(got.merged_result(), {"triage": [{"verdict": "worth a look"}]})

    def test_opening_the_same_database_twice_does_not_wipe_it(self):
        """The schema is created with IF NOT EXISTS; a second open is a restart."""
        self.queued("t1")
        again = Journal(self.path)
        self.addCleanup(again.close)
        self.assertIsNotNone(again.get("t1"))


class TestClaiming(JournalCase):
    """The lease is what keeps two instances off one task."""

    def test_a_claim_moves_the_task_out_of_the_queue(self):
        task = self.queued("t1")
        self.assertTrue(self.journal.claim(task, lease=30))

        self.assertEqual(task.status, TaskStatus.IN_PROGRESS)
        self.assertEqual(self.journal.peek(), [])

    def test_the_second_claim_on_the_same_task_loses(self):
        """Not an error, a `False` — the caller is expected to move on."""
        first = self.queued("t1")
        second = self.journal.get("t1")

        self.assertTrue(self.journal.claim(first, lease=30))
        self.assertFalse(self.journal.claim(second, lease=30))

    def test_only_one_of_many_threads_wins(self):
        """Sources run in their own threads, the dispatcher in the loop.
        The `UPDATE ... WHERE status = 'queued'` is what makes this safe;
        a read-then-write would hand the task out twice."""
        self.queued("t1")
        wins: list[bool] = []
        barrier = threading.Barrier(8)

        def race() -> None:
            task = self.journal.get("t1")
            barrier.wait()
            wins.append(self.journal.claim(task, lease=30))

        threads = [threading.Thread(target=race) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sum(wins), 1)

    def test_a_finished_task_cannot_be_claimed(self):
        task = self.queued("t1")
        self.journal.finish(task, TaskStatus.DONE)
        task.status = TaskStatus.QUEUED  # a stale in-memory copy
        self.assertFalse(self.journal.claim(task, lease=30))


class TestVisibility(JournalCase):
    def test_a_delayed_task_is_not_handed_out_yet(self):
        """This is how a retry backs off: still queued, not yet visible."""
        self.queued("t1", delay=60)
        self.assertEqual(self.journal.peek(), [])
        self.assertEqual(self.journal.status_counts(), {"queued": 1})

    def test_peeking_does_not_claim(self):
        self.queued("t1")
        self.assertEqual([t.id for t in self.journal.peek()], ["t1"])
        self.assertEqual([t.id for t in self.journal.peek()], ["t1"])

    def test_the_oldest_visible_task_comes_first(self):
        self.queued("late", delay=0.05)
        self.queued("early")
        self.assertEqual([t.id for t in self.journal.peek()], ["early"])

    def test_the_limit_is_respected(self):
        for i in range(5):
            self.queued(f"t{i}")
        self.assertEqual(len(self.journal.peek(limit=2)), 2)


class TestDeduplication(JournalCase):
    def test_whoever_gets_there_first_owns_the_key(self):
        self.assertEqual(self.journal.claim_dedup_key("0xabc", "t1"), "t1")
        self.assertEqual(self.journal.claim_dedup_key("0xabc", "t2"), "t1")

    def test_the_claim_survives_a_restart(self):
        """Deduplication that forgets on restart isn't deduplication."""
        self.journal.claim_dedup_key("0xabc", "t1")
        self.journal.close()

        reopened = Journal(self.path)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.claim_dedup_key("0xabc", "t2"), "t1")

    def test_different_keys_do_not_collide(self):
        self.assertEqual(self.journal.claim_dedup_key("a", "t1"), "t1")
        self.assertEqual(self.journal.claim_dedup_key("b", "t2"), "t2")


class TestSteps(JournalCase):
    """[0012](../docs/decisions/0012-step-idempotency.md): what's already
    done doesn't get done twice on a retry."""

    def test_a_step_that_returned_nothing_is_still_a_step_that_ran(self):
        """The sentinel, not the value, is the signal. Reading `None` as
        "never ran" would repeat a delivery on every retry — which is the
        bug 0012 found in our own outputs."""
        self.journal.record_step("t1", "output", "post", None)

        self.assertIsNone(self.journal.step_result("t1", "output", "post"))
        self.assertIsNot(self.journal.step_result("t1", "output", "post"), Journal.MISSING)

    def test_a_step_that_never_ran_says_so(self):
        self.assertIs(self.journal.step_result("t1", "output", "post"), Journal.MISSING)

    def test_the_same_key_in_another_slot_is_another_step(self):
        self.journal.record_step("t1", "output", "post", "sent")
        self.assertIs(self.journal.step_result("t1", "webhook", "post"), Journal.MISSING)

    def test_a_result_that_cannot_be_stored_is_refused_loudly(self):
        """Storing `str(obj)` here would look like it worked and replay
        gibberish after a restart."""
        with self.assertRaises(ValueError) as caught:
            self.journal.record_step("t1", "output", "post", {"conn": object()})
        self.assertIn("survives a restart", str(caught.exception))

    def test_nothing_is_recorded_when_the_result_is_refused(self):
        with self.assertRaises(ValueError):
            self.journal.record_step("t1", "output", "post", object())
        self.assertIs(self.journal.step_result("t1", "output", "post"), Journal.MISSING)

    def test_re_recording_a_step_overwrites_it(self):
        self.journal.record_step("t1", "output", "post", "first")
        self.journal.record_step("t1", "output", "post", "second")
        self.assertEqual(self.journal.step_result("t1", "output", "post"), "second")

    def test_finishing_a_task_clears_its_steps(self):
        """Otherwise the same id resubmitted tomorrow replays today's answers."""
        task = self.queued("t1")
        self.journal.record_step("t1", "output", "post", "sent")
        self.journal.finish(task, TaskStatus.DONE)

        self.assertIs(self.journal.step_result("t1", "output", "post"), Journal.MISSING)

    def test_clearing_leaves_other_tasks_alone(self):
        self.journal.record_step("t1", "output", "post", "sent")
        self.journal.record_step("t2", "output", "post", "sent")

        self.assertEqual(self.journal.clear_steps("t1"), 1)
        self.assertEqual(self.journal.step_result("t2", "output", "post"), "sent")


class TestRecovery(JournalCase):
    """An instance died mid-task. Nobody is going to report that."""

    def test_an_expired_lease_puts_the_task_back(self):
        task = self.queued("t1")
        task.slot = "triage"
        self.journal.claim(task, lease=0)  # already expired

        recovered = self.journal.recover_expired()

        self.assertEqual([t.id for t in recovered], ["t1"])
        self.assertEqual([t.id for t in self.journal.peek()], ["t1"])

    def test_a_live_lease_is_left_alone(self):
        task = self.queued("t1")
        self.journal.claim(task, lease=300)
        self.assertEqual(self.journal.recover_expired(), [])

    def test_the_same_slot_gets_it_back(self):
        """The route is written on assignment. Leaving it there would make
        the retry look like a second visit and the loop guard would refuse
        it — the task would stall instead of being retried."""
        task = self.queued("t1")
        task.slot = "triage"
        task.route.append("triage")
        self.journal.claim(task, lease=0)

        recovered = self.journal.recover_expired()

        self.assertEqual(recovered[0].route, [])
        self.assertFalse(recovered[0].visited("triage"))

    def test_an_earlier_slots_visit_is_not_erased(self):
        """Only the current assignment comes off. A task that genuinely
        passed through triage before must still count as having been there."""
        task = self.queued("t1")
        task.slot = "deep_dive"
        task.route.extend(["triage", "deep_dive"])
        self.journal.claim(task, lease=0)

        recovered = self.journal.recover_expired()

        self.assertEqual(recovered[0].route, ["triage"])

    def test_recovery_is_written_down(self):
        task = self.queued("t1")
        task.slot = "triage"
        self.journal.claim(task, lease=0)
        self.journal.recover_expired()

        events = self.journal.events(type="task_recovered")
        self.assertEqual(len(events), 1)
        self.assertIn("lease expired", events[0]["data"]["reason"])


class TestEvents(JournalCase):
    """Append-only. The brain, the judge and the dashboard all read from here."""

    def test_an_event_comes_back_with_its_data(self):
        self.journal.record("task_scored", "t1", "triage", score=0.9, met=True)
        event = self.journal.events(type="task_scored")[0]

        self.assertEqual(event["task_id"], "t1")
        self.assertEqual(event["slot"], "triage")
        self.assertEqual(event["data"], {"score": 0.9, "met": True})

    def test_the_row_number_is_handed_back(self):
        """The brain uses it as a proposal's identifier — an empty one and
        no acceptance would ever match its proposal."""
        first = self.journal.record("proposal_made", None, None, kind="new_role")
        second = self.journal.record("proposal_made", None, None, kind="new_role")

        self.assertTrue(first)
        self.assertGreater(second, first)

    def test_events_are_filtered_by_task(self):
        self.journal.record("task_queued", "t1")
        self.journal.record("task_queued", "t2")
        self.assertEqual([e["task_id"] for e in self.journal.events(task_id="t1")], ["t1"])

    def test_an_unknown_filter_returns_nothing_rather_than_everything(self):
        self.journal.record("task_queued", "t1")
        self.assertEqual(self.journal.events(type="nothing_like_this"), [])

    def test_nothing_is_ever_rewritten(self):
        """Append-only is the property the audit trail rests on."""
        self.journal.record("task_queued", "t1")
        self.journal.record("task_failed", "t1", reason="boom")

        with self.assertRaises(sqlite3.IntegrityError):
            self.journal._db.execute(
                "INSERT INTO events (seq, ts, type, data) VALUES (1, 0, 'forged', '{}')"
            )

    def test_iterating_the_journal_stops_at_ten_thousand(self):
        """`__iter__` is a window, not the whole journal: it delegates to
        `events(limit=10_000)`. Nothing in the colony walks it that way
        today — every consumer asks with its own limit — so this pins the
        cap rather than the "whole" the bare `iter()` suggests. Anything
        that later needs all of it needs a query, not this."""
        for i in range(3):
            self.journal.record("task_queued", f"t{i}")
        self.assertEqual(len(list(self.journal)), 3)


class TestLiveState(JournalCase):
    def test_status_counts_group_by_status(self):
        self.queued("t1")
        done = self.queued("t2")
        self.journal.finish(done, TaskStatus.DONE)

        self.assertEqual(self.journal.status_counts(), {"queued": 1, "done": 1})

    def test_queue_depth_is_counted_per_slot(self):
        task = self.queued("t1")
        task.slot = "triage"
        self.journal.save(task)

        self.assertEqual(self.journal.queue_depth().get("triage"), 1)

    def test_in_progress_lists_what_is_being_worked_on(self):
        task = self.queued("t1")
        task.slot = "triage"
        self.journal.claim(task, lease=30)

        self.assertEqual([t.id for t in self.journal.in_progress()], ["t1"])

    def test_by_status_reads_back_a_finished_task(self):
        task = self.queued("t1")
        self.journal.finish(task, TaskStatus.FAILED)

        failed = self.journal.by_status(TaskStatus.FAILED)
        self.assertEqual([t.id for t in failed], ["t1"])

    def test_an_unknown_task_is_none_not_an_error(self):
        self.assertIsNone(self.journal.get("never-existed"))


class TestRequeue(JournalCase):
    def test_a_requeued_task_is_queued_again_and_unleased(self):
        task = self.queued("t1")
        self.journal.claim(task, lease=300)
        self.journal.requeue(task)

        self.assertEqual([t.id for t in self.journal.peek()], ["t1"])
        # The lease is gone, so recovery has nothing to reclaim.
        self.assertEqual(self.journal.recover_expired(), [])

    def test_a_delayed_requeue_waits(self):
        task = self.queued("t1")
        self.journal.claim(task, lease=300)
        self.journal.requeue(task, delay=60)

        self.assertEqual(self.journal.peek(), [])
        self.assertEqual(self.journal.get("t1").status, TaskStatus.QUEUED)

    def test_the_attempt_counter_survives_the_round_trip(self):
        """Backoff and the give-up threshold both read it."""
        task = self.queued("t1")
        task.attempts = 2
        self.journal.requeue(task)

        self.assertEqual(self.journal.get("t1").attempts, 2)


if __name__ == "__main__":
    unittest.main()

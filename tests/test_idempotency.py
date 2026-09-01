"""Step idempotency: the worker gets called twice, the outside world once."""

from __future__ import annotations

import json
import socket
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from grokklings.journal import Journal
from grokklings.model import SlotSpec, TaskStatus
from grokklings.slots import WorkerContext
from tests import workers
from tests.helpers import ColonyFixture, slot


class StepCase(unittest.IsolatedAsyncioTestCase):
    fixture: ColonyFixture | None = None

    def setUp(self):
        workers.reset()

    def tearDown(self):
        if self.fixture:
            self.fixture.close()
            self.fixture = None

    def build(self, **raw):
        self.fixture = ColonyFixture(raw)
        return self.fixture

    async def run_one(self, payload=None):
        task = self.fixture.colony.submit(payload or {"url": "https://a"}).task
        await self.fixture.settle()
        return self.fixture.colony.journal.get(task.id)


class TestOnce(StepCase):
    async def test_side_effect_survives_retry_and_happens_once(self):
        self.build(slot=[slot("triage", "once_then_crash", max_attempts=3)])
        task = await self.run_one()

        self.assertEqual(task.status, TaskStatus.DONE)
        self.assertEqual(workers.calls["once_then_crash"], 2)   # the worker was called twice
        self.assertEqual(workers.effects["side_effect"], 1)     # the outside world, once

    async def test_replay_returns_the_recorded_result(self):
        self.build(slot=[slot("triage", "once_then_crash", max_attempts=3)])
        task = await self.run_one()

        # The second attempt got what the first one recorded, not a new value.
        recorded = task.merged_result()["triage"][0]["recorded"]
        self.assertEqual(recorded, {"name": "side_effect", "n": 1})

        types = [e["type"] for e in self.fixture.colony.journal.events(task_id=task.id)]
        self.assertIn("step_completed", types)
        self.assertIn("step_replayed", types)

    async def test_same_for_a_coroutine_worker(self):
        self.build(slot=[slot("triage", "once_async_then_crash", max_attempts=3)])
        task = await self.run_one()

        self.assertEqual(task.status, TaskStatus.DONE)
        self.assertEqual(workers.effects["coroutine"], 1)

    async def test_failed_step_is_not_recorded_and_repeats(self):
        # The other side of the coin: a failed step must repeat, or the
        # mechanism would turn into a way of losing work.
        self.build(slot=[slot("triage", "crashing_step", max_attempts=3)])
        task = await self.run_one()

        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(workers.effects["step_attempt"], 3)

    async def test_different_keys_are_independent(self):
        self.build(slot=[slot("triage", "two_keys_then_crash", max_attempts=3)])
        task = await self.run_one()

        self.assertEqual(task.status, TaskStatus.DONE)
        self.assertEqual(workers.effects["first"], 1)
        self.assertEqual(workers.effects["second"], 1)

    async def test_unserializable_result_is_rejected_clearly(self):
        self.build(slot=[slot("triage", "unserializable_step", max_attempts=1)])
        task = await self.run_one()

        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertIn("doesn't serialize", task.last_error)

    async def test_steps_are_cleared_when_the_task_ends(self):
        self.build(slot=[slot("triage", "once_then_crash", max_attempts=3)])
        task = await self.run_one()

        journal = self.fixture.colony.journal
        self.assertIs(journal.step_result(task.id, "triage", "side_effect"), Journal.MISSING)


class TestIdempotencyKey(unittest.TestCase):
    def context(self, task_id="t1", slot_name="triage", attempt=1):
        return WorkerContext(
            slot=SlotSpec(name=slot_name), instance=f"{slot_name}#0",
            journal=None, attempt=attempt, task_id=task_id,
        )

    def test_stable_across_attempts(self):
        self.assertEqual(
            self.context(attempt=1).idempotency_key("url"),
            self.context(attempt=3).idempotency_key("url"),
        )

    def test_differs_by_task_slot_and_name(self):
        base = self.context().idempotency_key("url")
        self.assertNotEqual(base, self.context(task_id="t2").idempotency_key("url"))
        self.assertNotEqual(base, self.context(slot_name="other").idempotency_key("url"))
        self.assertNotEqual(base, self.context().idempotency_key("other-url"))

    def test_context_without_task_says_so(self):
        ctx = WorkerContext(slot=SlotSpec(name="triage"), instance="triage#0", journal=None)
        with self.assertRaisesRegex(RuntimeError, "without a task_id"):
            ctx.idempotency_key()


class TestOutputsDoNotDuplicate(StepCase):
    async def test_file_output_writes_one_line_despite_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "archive.jsonl"
            self.build(
                slot=[slot("triage", "done")],
                output=[{
                    "name": "file",
                    "handler": "tests.workers:flaky_file_output",
                    "path": str(out),
                    "max_attempts": 3,
                }],
            )
            task = await self.run_one()

            self.assertEqual(task.status, TaskStatus.DONE)
            self.assertEqual(workers.calls["flaky_file_output"], 2)  # the output was called twice
            lines = out.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)                          # just one line
            self.assertEqual(json.loads(lines[0])["task"], task.id)

    async def test_webhook_output_posts_once_and_sends_a_stable_key(self):
        received: list[dict] = []

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                received.append({
                    "body": json.loads(self.rfile.read(length)),
                    "key": self.headers.get("Idempotency-Key"),
                })
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
        thread.start()
        try:
            self.build(
                slot=[slot("triage", "done")],
                output=[{
                    "name": "notify",
                    "handler": "tests.workers:flaky_webhook_output",
                    "url": f"http://127.0.0.1:{port}/notify",
                    "max_attempts": 3,
                }],
            )
            task = await self.run_one()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual(task.status, TaskStatus.DONE)
        self.assertEqual(workers.calls["flaky_webhook_output"], 2)  # the output was called twice
        self.assertEqual(len(received), 1)                          # only one request went out
        self.assertTrue(received[0]["key"])                         # an idempotency key was sent
        self.assertEqual(received[0]["body"]["task"], task.id)


if __name__ == "__main__":
    unittest.main()

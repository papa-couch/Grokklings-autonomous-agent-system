"""End-to-end run of phase 1: webhook -> intake -> dispatcher -> slot -> file output."""

from __future__ import annotations

import asyncio
import json
import socket
import tempfile
import unittest
import urllib.request
from pathlib import Path

from grokklings.colony import Colony
from grokklings.config import load

CONFIG = """
[colony]
name = "end-to-end"
db = "{db}"
poll_interval = 0.01

[intake]
dedup_key = "url"

[[source]]
name = "hook"
type = "webhook"
host = "127.0.0.1"
port = {port}
path = "/ingest"

[[slot]]
name = "triage"
description = "exercise the skeleton"
handler = "grokklings.handlers.echo:handle"
timeout = 5
entry = true
[slot.options]
note = "screened"

[[output]]
name = "file"
type = "file"
path = "{out}"
"""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def post(url: str, body: dict) -> tuple[int, dict]:
    # Bypass the environment's proxy: the address is local.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST"
    )
    with opener.open(request, timeout=5) as response:
        return response.status, json.loads(response.read())


class TestEndToEnd(unittest.IsolatedAsyncioTestCase):
    async def test_webhook_to_file_with_dedup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "out.jsonl"
            port = free_port()
            config_path = root / "colony.toml"
            config_path.write_text(
                CONFIG.format(db=root / "colony.db", port=port, out=out), encoding="utf-8"
            )

            colony = Colony(load(config_path))
            stop = asyncio.Event()
            runner = asyncio.create_task(colony.run(stop))
            url = f"http://127.0.0.1:{port}/ingest"
            await self._wait_for_server(port)

            try:
                first = await asyncio.to_thread(post, url, {"url": "https://a", "topic": "test"})
                second = await asyncio.to_thread(post, url, {"url": "https://a"})
                third = await asyncio.to_thread(post, url, {"url": "https://b"})

                self.assertEqual(first[0], 202)
                self.assertEqual(second[1]["status"], "duplicate")
                self.assertEqual(second[1]["duplicate_of"], first[1]["task"])
                self.assertEqual(third[0], 202)

                await self._wait_for_lines(out, 2)
            finally:
                stop.set()
                runner.cancel()
                await asyncio.gather(runner, return_exceptions=True)

            lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(lines), 2)  # the duplicate never reached the output
            self.assertEqual({line["result"]["triage"][0]["note"] for line in lines}, {"screened"})
            self.assertEqual(lines[0]["route"], ["triage", "file"])

            counts = colony.journal.status_counts()
            self.assertEqual(counts["done"], 2)
            self.assertEqual(counts["dropped"], 1)
            colony.close()

    async def _wait_for_server(self, port: int, attempts: int = 200) -> None:
        """Checks specifically that the socket is ready: probing with a real
        request would create an extra task and throw off the final count."""
        for _ in range(attempts):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    return
            except OSError:
                await asyncio.sleep(0.02)
        self.fail("the webhook never came up")

    async def _wait_for_lines(self, path: Path, expected: int, attempts: int = 200) -> None:
        for _ in range(attempts):
            if path.exists() and len(path.read_text(encoding="utf-8").splitlines()) >= expected:
                return
            await asyncio.sleep(0.02)
        self.fail(f"{path} never got {expected} lines")


if __name__ == "__main__":
    unittest.main()

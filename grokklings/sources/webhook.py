"""A webhook: incoming HTTP. A POST with a JSON body is one task.

The server lives in its own thread; intake is thread-safe.
The response tells the caller whether the task was accepted or is a duplicate.
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from ..intake import Intake
from . import Source, log

MAX_BODY = 4 * 1024 * 1024


class WebhookSource(Source):
    """Options: host, port, path, token (checked against the Authorization header)."""

    type = "webhook"

    def __init__(
        self,
        name: str,
        host: str = "127.0.0.1",
        port: int = 8099,
        path: str = "/ingest",
        token: str | None = None,
        **options: Any,
    ) -> None:
        super().__init__(name, host=host, port=port, path=path, **options)
        self.host, self.port, self.path, self.token = host, int(port), path, token
        self._server: ThreadingHTTPServer | None = None

    async def run(self, intake: Intake, stop: asyncio.Event) -> None:
        self._server = ThreadingHTTPServer(
            (self.host, self.port), _make_handler(self, intake)
        )
        thread = threading.Thread(target=lambda: self._server.serve_forever(poll_interval=0.1), name=f"webhook-{self.name}", daemon=True)
        thread.start()
        intake.journal.record(
            "source_started", source=self.name, kind=self.type,
            url=f"http://{self.host}:{self.port}{self.path}",
        )
        log.info("source %s listening on http://%s:%d%s", self.name, self.host, self.port, self.path)
        try:
            await stop.wait()
        finally:
            self._server.shutdown()
            self._server.server_close()
            thread.join(timeout=5)


def _make_handler(source: WebhookSource, intake: Intake) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:  # noqa: N802 — name set by the base class
            if self.path.split("?")[0] != source.path:
                return self._reply(404, {"error": "wrong path"})
            if source.token and self.headers.get("Authorization") != f"Bearer {source.token}":
                return self._reply(401, {"error": "unauthorized"})
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                return self._reply(400, {"error": "empty or too large a body"})
            try:
                payload = json.loads(self.rfile.read(length))
            except (ValueError, UnicodeDecodeError) as exc:
                return self._reply(400, {"error": f"couldn't parse JSON: {exc}"})

            admission = intake.submit(source.name, payload)
            if admission.accepted:
                return self._reply(202, {"task": admission.task.id, "status": "accepted"})
            return self._reply(
                200,
                {"task": admission.task.id, "status": "duplicate", "duplicate_of": admission.duplicate_of},
            )

        def _reply(self, code: int, body: dict[str, Any]) -> None:
            blob = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)

        def log_message(self, fmt: str, *args: Any) -> None:
            log.debug("webhook %s: " + fmt, source.name, *args)

    return Handler

"""Output to an outgoing webhook (a telegram bot, your own intake, anything with a URL)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from ..model import Task, Verdict
from ..slots import WorkerContext
from . import envelope


def handle(task: Task, ctx: WorkerContext) -> Verdict:
    url = ctx.options.get("url")
    if not url:
        return Verdict.fail("this output has no url set")
    body = envelope(task, ctx.options.get("shape", "merged"))

    headers = {"Content-Type": "application/json; charset=utf-8"}
    # The idempotency key is the same across every attempt at one delivery:
    # a service that honors it drops the repeat on its own — even the one
    # we never learned about because we crashed before hearing back.
    headers[ctx.options.get("idempotency_header", "Idempotency-Key")] = ctx.idempotency_key(url)
    headers.update(ctx.options.get("headers") or {})

    def deliver() -> int:
        data = json.dumps(body, ensure_ascii=False, default=str).encode()
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        # A network error isn't swallowed: the dispatcher decides about a
        # paced retry on its own. The step isn't recorded either way, so
        # delivery honestly retries.
        with urllib.request.urlopen(request, timeout=ctx.options.get("http_timeout", 15)) as response:
            return response.status

    code = ctx.once("delivery", deliver)
    return Verdict.done({"delivered_to": url, "status": code})

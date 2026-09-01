"""Scheduled polling: call the user's function once every N seconds."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

from ..intake import Intake
from ..slots import resolve
from . import Source, log


class IntervalSource(Source):
    """Options: seconds (the interval), handler ("module:function"), immediate.

    The user's function returns a list of payloads — one per task. An empty
    list is normal: it just means nothing new showed up.
    """

    type = "interval"

    def __init__(self, name: str, seconds: float = 60.0, handler: str = "", **options: Any) -> None:
        super().__init__(name, seconds=seconds, handler=handler, **options)
        if not handler:
            raise ValueError(f"source {name}: no handler set")
        self.seconds = float(seconds)
        self.fetch = resolve(handler)
        self.immediate = bool(options.get("immediate", True))

    async def run(self, intake: Intake, stop: asyncio.Event) -> None:
        intake.journal.record(
            "source_started", source=self.name, kind=self.type, seconds=self.seconds
        )
        if not self.immediate:
            await self._sleep(stop)
        while not stop.is_set():
            try:
                items = self.fetch(self.options)
                if inspect.isawaitable(items):
                    items = await items
                for payload in items or []:
                    self._admit(intake, self.name, payload)
            except Exception as exc:  # the poll failed — the colony keeps going
                log.warning("source %s: %s", self.name, exc)
                intake.journal.record(
                    "source_error", source=self.name, error=f"{type(exc).__name__}: {exc}"
                )
            await self._sleep(stop)

    async def _sleep(self, stop: asyncio.Event) -> None:
        try:
            await asyncio.wait_for(stop.wait(), timeout=self.seconds)
        except asyncio.TimeoutError:
            pass

"""Sources — where tasks come into the colony from.

A source decides nothing: it only hands payloads to intake. Everything else
(the id, the journal, deduplication, the queue) is intake's job.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..intake import Admission, Intake

log = logging.getLogger("grokklings.sources")


class Source:
    """The base source. Lives until the colony stops."""

    type = "base"

    def __init__(self, name: str, **options: Any) -> None:
        self.name = name
        self.options = options

    async def run(self, intake: Intake, stop: asyncio.Event) -> None:
        raise NotImplementedError

    @staticmethod
    def _admit(intake: Intake, name: str, payload: Any) -> Admission:
        admission = intake.submit(name, payload)
        if not admission.accepted:
            log.debug("duplicate %s -> %s", admission.task.id, admission.duplicate_of)
        return admission


class ManualSource(Source):
    """A manual submission: polls nothing, waits for submit() to be called.

    `python -m grokklings submit` works the same way — it writes to the same
    database, so a task can be submitted into a colony that's already running.
    """

    type = "manual"

    def __init__(self, name: str = "manual", **options: Any) -> None:
        super().__init__(name, **options)
        self._intake: Intake | None = None

    async def run(self, intake: Intake, stop: asyncio.Event) -> None:
        self._intake = intake
        intake.journal.record("source_started", source=self.name, kind=self.type)
        await stop.wait()

    def submit(self, payload: Any) -> Admission:
        if self._intake is None:
            raise RuntimeError("this source hasn't started yet")
        return self._admit(self._intake, self.name, payload)

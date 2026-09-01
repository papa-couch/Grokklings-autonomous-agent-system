from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from grokklings.colony import Colony
from grokklings.config import parse


class ColonyFixture:
    """A colony on a temporary database."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="grokklings-test-"))
        raw.setdefault("colony", {})["db"] = str(self.dir / "colony.db")
        raw["colony"].setdefault("retry_base", 0.01)
        raw["colony"].setdefault("poll_interval", 0.01)
        self.config = parse(raw)
        self.colony = Colony(self.config)

    def close(self) -> None:
        self.colony.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    async def settle(self, ticks: int = 400) -> None:
        """Turn the dispatcher's crank until no task is left unfinished.

        Counted by status, not by the visible queue: a task paused before a
        retry still counts as queued, but peek() won't hand it out yet.
        """
        import asyncio

        for _ in range(ticks):
            await self.colony.dispatcher.tick()
            await asyncio.sleep(0.005)
            counts = self.colony.journal.status_counts()
            unfinished = counts.get("queued", 0) + counts.get("in_progress", 0)
            if not unfinished and not self.colony.dispatcher._inflight:
                return
        await self.colony.dispatcher.drain()


def slot(name: str, handler: str, **extra: Any) -> dict[str, Any]:
    spec = {"name": name, "handler": f"tests.workers:{handler}"}
    spec.update(extra)
    return spec

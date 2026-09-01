"""Assembling a colony from its config and running it."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from .config import BUILTIN_SOURCES, ColonyConfig, ConfigError
from .dispatcher import Dispatcher
from .intake import Admission, Intake
from .journal import Journal
from .model import TaskStatus
from . import cost as cost_module
from . import scaling
from .routing import Router
from .slots import SlotRuntime, resolve
from .sources import Source

log = logging.getLogger("grokklings.colony")

DEFAULT_ROUTER = "grokklings.routing:GrokRouter"


def build_source(entry: dict[str, Any]) -> Source:
    options = dict(entry)
    name = options.pop("name")
    kind = options.pop("type", "manual")
    path = options.pop("class", None) or BUILTIN_SOURCES[kind]
    factory = resolve(path)
    try:
        return factory(name=name, **options)
    except TypeError as exc:
        raise ConfigError(f"source {name}: {exc}") from exc


def build_guard(config: ColonyConfig, journal: Any) -> cost_module.Guard | None:
    """A colony with no [cost] section keeps no accounts and has no limit.

    A section with prices but no limit is legal and useful: `grokklings cost`
    reports, nothing ever pauses.
    """
    budget = cost_module.budget_from(config.cost)
    if budget is None or not budget:
        return None
    prices_path = Path(config.db).parent / budget.prices
    try:
        prices = cost_module.Prices.load(prices_path)
    except cost_module.PricesMissing as exc:
        # Refusing to start is right: a limit that silently isn't enforced
        # is worse than no limit, because it was asked for.
        raise ConfigError(f"[cost]: a limit is set but {exc}") from exc
    return cost_module.Guard(budget, prices, journal)


def build_router(routing: dict[str, Any] | None) -> Router | None:
    """A colony with no [routing] section gets no router — and behaves
    exactly as it did before there was one. An empty `[routing]` section is
    a different thing: it asks for the default router."""
    if routing is None:
        return None
    options = dict(routing)
    path = options.pop("router", DEFAULT_ROUTER)
    factory = resolve(path)
    try:
        return factory(**options)
    except TypeError as exc:
        raise ConfigError(f"[routing]: {exc}") from exc


class Colony:
    def __init__(self, config: ColonyConfig) -> None:
        self.config = config
        self.journal = Journal(config.db)
        # From here on the database is open, and several of these steps can
        # refuse: a [cost] limit with no price table is meant to stop the
        # colony starting. Letting that escape without closing the journal
        # leaks the handle and leaves WAL files behind — once per attempt,
        # so a supervisor retrying a bad config leaks steadily.
        try:
            self._assemble(config)
        except BaseException:
            self.journal.close()
            raise

    def _assemble(self, config: ColonyConfig) -> None:
        self.intake = Intake(self.journal, config.dedup_key)
        policy = scaling.policy_from(config.scaling)
        # An output isn't scaled from the queue, so it keeps its own count.
        start = policy.min_instances if policy else None
        self.slots: dict[str, SlotRuntime] = {
            spec.name: SlotRuntime(spec, self.journal, None if spec.terminal else start)
            for spec in config.all_slots
        }
        self.sources: list[Source] = [build_source(s) for s in config.sources]
        self.dispatcher = Dispatcher(
            journal=self.journal,
            slots=self.slots,
            entry=config.entry,
            entries=config.entries,
            router=build_router(config.routing),
            scaler=scaling.Scaler(policy, self.journal, config.entry) if policy else None,
            guard=build_guard(config, self.journal),
            outputs=[spec.name for spec in config.outputs],
            max_hops=config.max_hops,
            retry_base=config.retry_base,
            poll_interval=config.poll_interval,
            on_alert=self._alert,
        )

    # A notification to the user. Phase 6 plugs a real channel in here —
    # right now it's the journal plus the log.
    def _alert(self, kind: str, data: dict[str, Any]) -> None:
        self.journal.record("alert", data.get("task"), kind=kind, **data)
        log.warning("colony %s: %s %s", self.config.name, kind, data)

    def submit(self, payload: Any, source: str = "manual") -> Admission:
        """A manual submission — works even on a stopped colony."""
        return self.intake.submit(source, payload)

    async def run(self, stop: asyncio.Event | None = None) -> None:
        stop = stop or asyncio.Event()
        jobs = [asyncio.create_task(self.dispatcher.run(stop), name="dispatcher")]
        for source in self.sources:
            jobs.append(asyncio.create_task(self.source_loop(source, stop), name=f"source:{source.name}"))
        log.info(
            "colony %s is up: slots %s, entry %s",
            self.config.name, ", ".join(self.slots), self.config.entry,
        )
        try:
            await asyncio.gather(*jobs)
        finally:
            stop.set()
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)

    async def source_loop(self, source: Source, stop: asyncio.Event) -> None:
        try:
            await source.run(self.intake, stop)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("source %s stopped: %s", source.name, exc)
            self.journal.record("source_stopped", source=source.name, error=f"{type(exc).__name__}: {exc}")
            self._alert("source_down", {"source": source.name, "error": str(exc)})

    def status(self) -> dict[str, Any]:
        """Live state — what a phase 6 dashboard would grow out of."""
        return {
            "colony": self.config.name,
            "paused": self.dispatcher.paused,
            "slots": [
                {
                    "name": name,
                    "instances": len(rt.instances),
                    "busy": rt.load,
                    "terminal": rt.spec.terminal,
                    "handled": sum(i.handled for i in rt.instances),
                }
                for name, rt in self.slots.items()
            ],
            "queue_depth": self.journal.queue_depth(),
            "in_progress": [
                {"task": t.id, "slot": t.slot, "attempt": t.attempts} for t in self.journal.in_progress()
            ],
            "tasks": self.journal.status_counts(),
            "unplaced": len(self.journal.by_status(TaskStatus.UNPLACED, limit=1000)),
        }

    def close(self) -> None:
        self.journal.close()

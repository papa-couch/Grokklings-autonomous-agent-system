"""The user-supplied colony config (TOML).

Built into the system, not configurable: intake, dispatcher, journal,
stop control. Supplied by the user: sources, worker slots, context, output points.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .dispatcher import DEFAULT_MAX_HOPS
from .intake import WHOLE_PAYLOAD
from .model import SlotSpec

BUILTIN_OUTPUTS = {
    "file": "grokklings.outputs.file:handle",
    "console": "grokklings.outputs.console:handle",
    "webhook": "grokklings.outputs.webhook:handle",
}

BUILTIN_SOURCES = {
    "manual": "grokklings.sources:ManualSource",
    "interval": "grokklings.sources.interval:IntervalSource",
    "webhook": "grokklings.sources.webhook:WebhookSource",
}

_SLOT_KEYS = {
    "name", "description", "context", "tools", "handler", "timeout",
    "max_instances", "can_route_to", "entry", "max_attempts", "options",
    "success",
}
_OUTPUT_KEYS = _SLOT_KEYS | {"type", "when"}


class ConfigError(ValueError):
    """The config doesn't assemble into a working colony."""


@dataclass
class ColonyConfig:
    name: str = "grokklings"
    db: str = "colony.db"
    entry: str | None = None
    entries: list[str] = field(default_factory=list)
    # None means the section is absent; an empty dict means it's present
    # with no keys, which is a request for the defaults — not the same thing.
    routing: dict[str, Any] | None = None
    success: dict[str, Any] = field(default_factory=dict)
    scaling: dict[str, Any] | None = None
    cost: dict[str, Any] | None = None
    max_hops: int = DEFAULT_MAX_HOPS
    retry_base: float = 1.0
    poll_interval: float = 0.05
    dedup_key: str | None = WHOLE_PAYLOAD
    sources: list[dict[str, Any]] = field(default_factory=list)
    slots: list[SlotSpec] = field(default_factory=list)
    outputs: list[SlotSpec] = field(default_factory=list)

    @property
    def all_slots(self) -> list[SlotSpec]:
        return [*self.slots, *self.outputs]

    def slot_names(self) -> list[str]:
        return [s.name for s in self.all_slots]


def load(path: str | Path) -> ColonyConfig:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config not found: {path}")
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    return parse(raw)


def parse(raw: dict[str, Any]) -> ColonyConfig:
    colony = raw.get("colony") or {}
    intake = raw.get("intake") or {}
    config = ColonyConfig(
        name=colony.get("name", "grokklings"),
        db=colony.get("db", "colony.db"),
        entry=colony.get("entry"),
        routing=dict(raw["routing"] or {}) if "routing" in raw else None,
        success=dict(raw.get("success") or {}),
        scaling=dict(raw["scaling"] or {}) if "scaling" in raw else None,
        cost=dict(raw["cost"] or {}) if "cost" in raw else None,
        max_hops=int(colony.get("max_hops", DEFAULT_MAX_HOPS)),
        retry_base=float(colony.get("retry_base", 1.0)),
        poll_interval=float(colony.get("poll_interval", 0.05)),
        dedup_key=intake.get("dedup_key", WHOLE_PAYLOAD) or None,
        sources=list(raw.get("source") or []),
        slots=[_slot(entry, terminal=False) for entry in raw.get("slot") or []],
        outputs=[_slot(entry, terminal=True) for entry in raw.get("output") or []],
    )
    _validate(config)
    return config


def _slot(entry: dict[str, Any], terminal: bool) -> SlotSpec:
    kind = "output" if terminal else "slot"
    name = entry.get("name")
    if not name:
        raise ConfigError(f"one of the {kind}s has no name")

    handler = entry.get("handler")
    if terminal and not handler:
        output_type = entry.get("type", "console")
        handler = BUILTIN_OUTPUTS.get(output_type)
        if handler is None:
            known = ", ".join(BUILTIN_OUTPUTS)
            raise ConfigError(f"output {name}: unknown type={output_type!r}, known ones: {known}")
    if not handler:
        raise ConfigError(f"slot {name}: no handler set")

    known_keys = _OUTPUT_KEYS if terminal else _SLOT_KEYS
    options = dict(entry.get("options") or {})
    options.update({k: v for k, v in entry.items() if k not in known_keys})
    if terminal and entry.get("when"):
        options["when"] = entry["when"]

    return SlotSpec(
        name=name,
        description=entry.get("description", ""),
        context=entry.get("context", ""),
        success=entry.get("success", ""),
        tools=list(entry.get("tools") or []),
        handler=handler,
        timeout=float(entry.get("timeout", 60.0)),
        max_instances=int(entry.get("max_instances", 1)),
        can_route_to=list(entry.get("can_route_to") or []),
        entry=bool(entry.get("entry", False)),
        terminal=terminal,
        max_attempts=int(entry.get("max_attempts", 3)),
        options=options,
    )


def _validate(config: ColonyConfig) -> None:
    if not config.slots:
        raise ConfigError("colony with no worker slots: add at least one [[slot]]")

    names = config.slot_names()
    duplicates = {n for n in names if names.count(n) > 1}
    if duplicates:
        raise ConfigError(f"slot names repeat: {', '.join(sorted(duplicates))}")

    for spec in config.all_slots:
        unknown = [t for t in spec.can_route_to if t not in names]
        if unknown:
            raise ConfigError(f"slot {spec.name} refers to non-existent slots: {', '.join(unknown)}")
        if spec.max_instances < 1:
            raise ConfigError(f"slot {spec.name}: max_instances must be at least 1")
        if spec.timeout <= 0:
            raise ConfigError(f"slot {spec.name}: timeout must be greater than zero")

    if config.entry is None:
        marked = [s.name for s in config.slots if s.entry]
        if len(marked) > 1:
            # Several entry slots need someone to choose between them.
            if config.routing is None:
                raise ConfigError(
                    f"several slots marked as entry ({', '.join(marked)}) but no [routing] "
                    "section: either leave one entry slot, or configure a router to pick"
                )
            config.entries = marked
            config.entry = marked[0]
        elif marked:
            config.entry = marked[0]
        elif len(config.slots) == 1:
            config.entry = config.slots[0].name
        else:
            raise ConfigError(
                "can't tell where a new task goes: mark a slot entry = true "
                "or set colony.entry"
            )
    elif config.entry not in names:
        raise ConfigError(f"entry slot {config.entry!r} isn't defined")

    if not config.entries and config.entry:
        config.entries = [config.entry]

    for source in config.sources:
        if not source.get("name"):
            raise ConfigError("one of the sources has no name")
        kind = source.get("type", "manual")
        if kind not in BUILTIN_SOURCES and not source.get("class"):
            known = ", ".join(BUILTIN_SOURCES)
            raise ConfigError(
                f"source {source['name']}: unknown type={kind!r} "
                f"(known ones: {known}; a custom source is set via the class key)"
            )

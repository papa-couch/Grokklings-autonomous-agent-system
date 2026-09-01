"""Speaking OpenTelemetry's vocabulary without giving up our own.

[0007](../docs/decisions/0007-otel-event-names.md) proposed renaming journal
events onto the OpenTelemetry GenAI convention so a dashboard could read
them. This does the job the other way round: **the journal keeps its own
names, and this module translates.**

The reason is in 0007's own text — most of our events have nowhere to go in
the convention. `task_unplaced`, `task_looped`, `step_replayed`,
`proposal_made`, `scaling_decision` are the vocabulary of this design, and
renaming them to something OTel recognises would lose what they mean while
breaking every journal already on disk. Five of our events have honest
equivalents. The rest stay ours and simply aren't exported.

Two more reasons the translation is the safer shape:

- **The convention is at Development status** — verified from the spec
  itself, not assumed. Operation and attribute names can still change. When
  they do, this one file changes and no stored data does.
- **Adopting no dashboard costs nothing.** Nothing in the colony imports
  this module; it reads the journal from outside, like the brain does.

Deliberately not done: this emits OTLP-*shaped* records, not OTLP. There's
no collector, no protobuf, no gRPC. It's the shape a collector or a
dashboard's importer expects, written to a file, and calling it an exporter
would oversell it.
"""

from __future__ import annotations

from typing import Any, Callable, Iterator

# Status of the convention as read from the spec on 2026-08-27. Not stable:
# https://github.com/open-telemetry/semantic-conventions-genai
CONVENTION_STATUS = "Development"


class Op:
    """The convention's operation names, in one place so a rename is one edit."""

    CHAT = "chat"
    CREATE_AGENT = "create_agent"
    EXECUTE_TOOL = "execute_tool"
    INVOKE_AGENT = "invoke_agent"
    INVOKE_WORKFLOW = "invoke_workflow"
    PLAN = "plan"


class Attr:
    """The convention's attribute names, likewise."""

    OPERATION = "gen_ai.operation.name"      # required
    PROVIDER = "gen_ai.provider.name"        # required
    AGENT_NAME = "gen_ai.agent.name"
    AGENT_ID = "gen_ai.agent.id"
    MODEL = "gen_ai.request.model"
    CONVERSATION = "gen_ai.conversation.id"
    INPUT_TOKENS = "gen_ai.usage.input_tokens"
    OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
    ERROR_TYPE = "error.type"                # from the stable base convention


# The registry spells xAI's provider `x_ai`, not `xai` — checked against
# docs/registry/attributes/gen-ai.md rather than guessed.
PROVIDERS = {"grok": "x_ai", "claude": "anthropic", "gpt": "openai"}


def provider_for(model: str | None) -> str | None:
    """Which provider a model name belongs to, or None if we can't tell.

    None rather than a guess: `gen_ai.provider.name` is a required attribute
    with a registry of allowed values, and inventing one produces a span
    that looks conformant and isn't.
    """
    if not model:
        return None
    lowered = model.lower()
    for prefix, name in PROVIDERS.items():
        if prefix in lowered:
            return name
    return None


def _drop_empty(attributes: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in attributes.items() if v is not None}


# ------------------------------------------------------------------ mapping


def _invoke_agent(event: dict[str, Any]) -> dict[str, Any]:
    data = event.get("data") or {}
    return _drop_empty(
        {
            Attr.OPERATION: Op.INVOKE_AGENT,
            Attr.AGENT_NAME: event.get("slot"),
            Attr.AGENT_ID: data.get("instance"),
            Attr.CONVERSATION: event.get("task_id"),
        }
    )


def _worker_result(event: dict[str, Any]) -> dict[str, Any]:
    data = event.get("data") or {}
    attributes = _invoke_agent(event)
    # A worker's own "fail" verdict is an error the convention has a stable
    # attribute for; the other three verdicts are ordinary outcomes.
    if data.get("decision") == "fail":
        attributes[Attr.ERROR_TYPE] = data.get("reason") or "fail"
    return attributes


def _chat(event: dict[str, Any]) -> dict[str, Any] | None:
    """A model call. This is the one that carries usage, and therefore cost."""
    data = event.get("data") or {}
    model = data.get("model")
    if model is None or data.get("input_tokens") is None:
        # A worker_note is a free-form note; only the ones a model-backed
        # worker writes are a chat span. The rest aren't.
        return None
    return _drop_empty(
        {
            Attr.OPERATION: Op.CHAT,
            Attr.PROVIDER: provider_for(model),
            Attr.MODEL: model,
            Attr.AGENT_NAME: event.get("slot"),
            Attr.AGENT_ID: data.get("instance"),
            Attr.CONVERSATION: event.get("task_id"),
            Attr.INPUT_TOKENS: data.get("input_tokens"),
            Attr.OUTPUT_TOKENS: data.get("output_tokens"),
        }
    )


def _create_agent(event: dict[str, Any]) -> dict[str, Any]:
    data = event.get("data") or {}
    return _drop_empty(
        {
            Attr.OPERATION: Op.CREATE_AGENT,
            Attr.AGENT_NAME: event.get("slot"),
            Attr.AGENT_ID: data.get("instance"),
        }
    )


def _plan(event: dict[str, Any]) -> dict[str, Any]:
    """The router choosing who handles a task is a planning step."""
    return _drop_empty(
        {
            Attr.OPERATION: Op.PLAN,
            Attr.AGENT_NAME: event.get("slot"),
            Attr.CONVERSATION: event.get("task_id"),
        }
    )


# Our event name -> how to say it in the convention. Everything absent from
# this table stays ours, on purpose: a forced mapping is worse than none,
# because it claims a meaning the convention doesn't actually carry.
MAPPING: dict[str, Callable[[dict[str, Any]], dict[str, Any] | None]] = {
    "task_assigned": _invoke_agent,
    "worker_result": _worker_result,
    "worker_note": _chat,
    "instance_spawned": _create_agent,
    "slot_picked": _plan,
}

# Named so the reason is greppable, and so a reader can see the split is a
# decision rather than an oversight.
OURS_ALONE = (
    "task_received", "task_queued", "task_duplicate", "task_routed", "task_rehomed",
    "task_done", "task_failed", "task_unplaced", "task_looped", "task_returned",
    "task_recovered", "retry_scheduled", "step_completed", "step_replayed",
    "instance_collapsed", "scaling_decision", "slot_pick_failed", "task_scored",
    "judge_calibrated", "proposal_made", "proposal_accepted", "proposal_rejected",
    "colony_started", "colony_stopped", "source_started", "source_stopped",
    "source_error", "alert",
    # A budget is this design's idea. The convention has no notion of one.
    "cost_limit_reached",
)


def to_span(event: dict[str, Any]) -> dict[str, Any] | None:
    """One journal event as an OTLP-shaped record, or None if it stays ours."""
    build = MAPPING.get(event.get("type", ""))
    if build is None:
        return None
    attributes = build(event)
    if attributes is None:
        return None
    name = attributes.get(Attr.AGENT_NAME)
    operation = attributes[Attr.OPERATION]
    return {
        # Span name per the convention: "{operation} {agent name}".
        "name": f"{operation} {name}" if name else operation,
        "trace_id": event.get("task_id"),
        "span_id": str(event.get("seq")),
        "timestamp": event.get("ts"),
        "attributes": attributes,
    }


def spans(events: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for event in events:
        span = to_span(event)
        if span is not None:
            yield span


def coverage(events: list[dict[str, Any]]) -> dict[str, int]:
    """How much of a journal speaks the shared vocabulary, and how much doesn't.

    Worth reporting rather than hiding: a reader who exports a journal and
    sees five spans out of two hundred events should be told that's the
    design, not a failure.
    """
    exported = untranslated = 0
    for event in events:
        if to_span(event) is not None:
            exported += 1
        else:
            untranslated += 1
    return {"exported": exported, "ours_only": untranslated, "total": len(events)}

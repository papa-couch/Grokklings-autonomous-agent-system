# Going public

The repository is private. Everything below is what stands between it and
being useful to someone else, in the order it needs doing. Nothing here is
code — it is a handful of settings that no commit can make for you, which
is why they are written down rather than done.

## 1. About — description

Repository home page → the gear beside **About**. Paste:

```
Recurring work, split into stations — each one a Grok-backed agent. Runs unattended; never changes itself without your say-so.
```

126 characters, and the part that survives truncation in search listings
is the first sentence. It deliberately does not open on "autonomous agent
system": the repository name already says that, and a description that
repeats the name spends its best characters on nothing. This one adds the
shape of the thing and the searchable word instead.

## 2. About — topics

Same panel. Fifteen, each one true of the code rather than decoration:

```
grok  xai  ai-agents  autonomous-agents  multi-agent  agent-framework
llm-agents  agent-orchestration  automation  workflow-automation
python  task-queue  sqlite  opentelemetry  structured-outputs
```

| topic | what backs it |
|---|---|
| `grok`, `xai` | `api.x.ai`, the primary backend |
| `autonomous-agents` | the colony runs its own loop — sources, dispatch, scoring — with nobody at the keyboard |
| `structured-outputs` | `response_format` with a `json_schema` and `strict: true` |
| `task-queue`, `sqlite` | the queue is SQLite: `enqueue` / `peek` / `claim` |
| `opentelemetry` | `grokklings export`, and `grokklings/otel.py` |
| `agent-orchestration` | the dispatcher, routing on verdicts |

## 3. Flip it to public

Settings → General → Danger Zone → Change visibility.

Worth doing after the two steps above, so the first person to look at a
public repository sees a finished front page rather than a work in
progress. The licence, the README and CI are already on `main`.

## 4. Social preview

Settings → General → Social preview — what shows when the link is pasted
into Slack, X or Telegram. It wants 1280×640; the banner is 2.5:1, so
upload [`docs/img/social-preview.png`](img/social-preview.png) instead —
the same artwork fitted to that frame on black, nothing cropped.

## Why this file exists

None of the above can be committed. Repository settings live outside the
git history, so they are the one part of shipping that no amount of work in
the tree can finish — and the one part that gets forgotten. Written down
here so it survives being remembered.

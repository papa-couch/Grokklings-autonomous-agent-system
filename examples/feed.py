"""An example scheduled-polling source.

The function gets the source's options from the config and returns a list of
payloads — one per task. Intake handles deduplication, so it's fine to hand
back the same items again here: repeats get filtered out by key.
"""

from __future__ import annotations

from typing import Any


def fetch(options: dict[str, Any]) -> list[dict[str, Any]]:
    # A real source would go out to HTTP, RSS, or a database here.
    return [
        {"url": "https://example.com/one", "title": "first"},
        {"url": "https://example.com/two", "title": "second"},
    ]

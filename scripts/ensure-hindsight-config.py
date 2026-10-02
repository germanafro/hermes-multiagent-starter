#!/usr/bin/env python3
"""Pin the Hindsight LLM base URL in every hindsight config.json (idempotent).

Why this exists
---------------
The plugin collapses provider ``openrouter`` onto the daemon's ``openai`` wire
(``settings.py::_OPENAI_WIRE_PROVIDERS``) and then relies on
``HINDSIGHT_API_LLM_BASE_URL`` to tell the daemon where ``openai`` actually lives.
With no base URL the daemon sends the OpenRouter key to ``api.openai.com`` and every
retain fails: ``401 Incorrect API key provided: sk-or-v1...``.

The plugin's own setup wizard writes exactly this value
(``plugins/hindsight/setup.py`` -> ``https://openrouter.ai/api/v1``); when that branch
never runs, the file is simply left without it.

A hand fix does not survive, which is why this runs on every reconcile:
  * a session reads ``config.json`` once, in ``initialize()`` - editing it does not
    reach an already-running session, and
  * the daemon's start path can rewrite the profile env file from the config it
    loaded at startup, erasing a line added by hand.

Read-only unless the key is genuinely missing and the provider is openrouter.
"""

from __future__ import annotations

import glob
import json
import os
import sys

BASE_URL = "https://openrouter.ai/api/v1"
PATTERNS = (
    "/opt/data/hindsight/config.json",             # default profile
    "/opt/data/profiles/*/hindsight/config.json",  # every named profile
)


def pin(path: str) -> None:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        # Corrupt or empty: the plugin falls through to the next config source. Leave it be.
        return
    if not isinstance(data, dict):
        return
    if data.get("llm_provider") != "openrouter":
        return          # only openrouter needs the override
    if data.get("llm_base_url"):
        return          # already pinned (or deliberately pointed elsewhere)
    data["llm_base_url"] = BASE_URL
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    print(f"  pinned llm_base_url in {path}")


def main() -> int:
    seen = 0
    for pattern in PATTERNS:
        for path in sorted(glob.glob(pattern)):
            seen += 1
            pin(path)
    if not seen:
        print("  no hindsight config.json found yet (profiles not provisioned?)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

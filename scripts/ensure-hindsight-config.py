#!/usr/bin/env python3
"""Keep every hindsight config.json correct and portable (idempotent).

Two jobs, both filling only what is MISSING - an explicit value is never overwritten,
so the declarative choice lives in overlay/profiles/<name>/hindsight/config.json and
this script is only the safety net (it also covers the default profile, which no
overlay provisions).

1. ``llm_base_url`` - the plugin collapses provider ``openrouter`` onto the daemon's
   ``openai`` wire (settings.py::_OPENAI_WIRE_PROVIDERS) and then relies on
   HINDSIGHT_API_LLM_BASE_URL. Unset, the daemon sends the OpenRouter key to
   api.openai.com and every retain 401s while /health still says "healthy". The plugin's
   own setup wizard writes ``https://openrouter.ai/api/v1``; when that branch never ran
   the file is simply left without it.

2. ``retain_source`` - attribution for a SHARED bank. It becomes ``metadata.source`` on
   every retained memory, which is what lets one bank tell its writers apart (and lets
   consolidation and audit answer "who learned this?"). Opt-in by design: the plugin
   ships ``_DEFAULT_RETAIN_SOURCE = ""``. Label = the profile name, so the value matches
   the agent that wrote the memory - including profiles created later.

Running from apply-overlay.sh (the container healthcheck) is what keeps this true: a
session reads config.json once, in initialize(), and the daemon start path can rewrite a
hand-edited line from the config it loaded at startup.

Ownership matters here and is preserved. The healthcheck runs as root while the plugin
runs as uid 1000 and keeps its profile configs owner-only (0600); a rewrite that left a
root-owned 0644 file behind would both lock the plugin out of its own config and trip
its owner-only validation. So the replacement inherits the original uid/gid/mode, and a
non-root caller refuses to touch a file it does not own rather than clobber it.
"""
import json
import os
import sys
from glob import glob

BASE_URL = "https://openrouter.ai/api/v1"
OPENAI_WIRE_PROVIDERS = {"openrouter", "openai_compatible"}
DEFAULT_PROFILE_LABEL = "hermes"  # the default profile's config lives at /opt/data
CONFIG_GLOBS = ("/opt/data/hindsight/config.json", "/opt/data/profiles/*/hindsight/config.json")


def label_for(path: str) -> str:
    """Attribution label: profile name, or DEFAULT_PROFILE_LABEL for the default profile.

    /opt/data/profiles/marvin/hindsight/config.json -> marvin
    /opt/data/hindsight/config.json                 -> hermes
    """
    parts = os.path.normpath(path).split(os.sep)
    if "profiles" in parts:
        i = parts.index("profiles")
        if i + 1 < len(parts):
            return parts[i + 1]
    return DEFAULT_PROFILE_LABEL


def write_preserving_ownership(path: str, cfg: dict) -> str:
    """Replace path atomically, keeping its original uid/gid/mode. '' on success, else why not."""
    try:
        st = os.stat(path)
    except OSError as exc:
        return f"cannot stat: {exc}"

    if os.geteuid() != 0 and st.st_uid != os.geteuid():
        return f"not owned by uid {os.geteuid()} - use root (the healthcheck runs as root)"

    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.chmod(tmp, st.st_mode & 0o7777)
        if os.geteuid() == 0:
            os.chown(tmp, st.st_uid, st.st_gid)
        os.replace(tmp, path)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return f"write failed: {exc}"
    return ""


def pin(path: str) -> int:
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return 0  # unreadable/corrupt: leave it alone, the plugin falls back
    if not isinstance(cfg, dict):
        return 0

    changed = []
    if cfg.get("llm_provider") in OPENAI_WIRE_PROVIDERS and not cfg.get("llm_base_url"):
        cfg["llm_base_url"] = BASE_URL
        changed.append("llm_base_url")
    if not cfg.get("retain_source"):
        cfg["retain_source"] = label_for(path)
        changed.append("retain_source")

    if not changed:
        return 0

    problem = write_preserving_ownership(path, cfg)
    if problem:
        print(f"  skipped {path}: {problem}")
        return 0
    st = os.stat(path)
    print(f"  pinned {', '.join(changed)} in {path} (kept {st.st_uid}:{st.st_gid} {oct(st.st_mode & 0o777)})")
    return 1


def main() -> int:
    seen, touched = 0, 0
    for pattern in CONFIG_GLOBS:
        for path in sorted(glob(pattern)):
            if not os.path.isfile(path) or path.endswith(".tmp"):
                continue
            seen += 1
            touched += pin(path)
    if seen == 0:
        print("  no hindsight configs found yet - nothing to pin")
    else:
        print(f"  {seen} config(s) checked, {touched} updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())

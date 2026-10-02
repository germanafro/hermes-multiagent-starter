#!/bin/bash
# apply-overlay.sh - Declarative Hermes profile reconciler (non-destructive)
#
# Enforces overlay/profiles/<name> onto the live profiles WITHOUT wiping them:
# stateful data (sessions, state.db, memories, cron, logs) survives every run.
# New profiles are cloned once from the working global defaults; existing
# profiles are left alone except for the force-copy of declared files.
# Safe to invoke repeatedly (it is wired as the container healthcheck).

MAPPED_OVERLAY_DIR="/app/overlay"          # Your mounted root profiles folder
HERMES_DATA_DIR="/opt/data"                # Where Hermes expects profiles to live

echo "🤖 Reconciling overlay profiles (state preserved)..."

if [ ! -d "$MAPPED_OVERLAY_DIR/profiles" ]; then
    echo "⚠️ Warning: Source directory $MAPPED_OVERLAY_DIR/profiles not found!"
    exit 1
fi

# Iterate through each custom configuration subdirectory
for item in "$MAPPED_OVERLAY_DIR"/profiles/*; do
    if [ -d "$item" ]; then
        profile_name=$(basename "$item")
        target="$HERMES_DATA_DIR/profiles/$profile_name"

        if [ ! -d "$target" ]; then
            echo "🚀 Provisioning new profile: [$profile_name]"
            # Clone the working global defaults (config/environments inherited)
            if ! hermes profile create "$profile_name" --clone; then
                echo "❌ profile create failed for [$profile_name]; skipping copy"
                continue
            fi
        else
            echo "♻️ Profile exists: [$profile_name] (state kept)"
        fi

        # Force-copy custom overrides (SOUL.md, profile.yaml, ...) over the top.
        # NOTE: this also resets anything the Desktop or bot runtime may have
        # written into these same files (e.g. profile.yaml ui_meta extras).
        # Only declare files here you want pinned; everything else is state.
        echo "📝 Enforcing declared files for [$profile_name]..."
        cp -rf "$item"/. "$target"/
    fi
done

# Pin the Hindsight LLM base URL on every config that needs it (idempotent; a no-op when set).
# See docs/hindsight-memory.md - without it the daemon 401s against api.openai.com.
PY_BIN=/opt/hermes/.venv/bin/python
[ -x "$PY_BIN" ] || PY_BIN=python3
"$PY_BIN" /app/scripts/ensure-hindsight-config.py || echo "⚠️ hindsight config check skipped"

echo "✅ Reconciliation complete."

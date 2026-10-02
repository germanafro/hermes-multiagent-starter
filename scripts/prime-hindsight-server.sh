#!/bin/bash
# prime-hindsight-server.sh - install the Hindsight memory server ONCE, uninterrupted.
#
# RUN INSIDE THE CONTAINER, AS THE SERVICE USER:
#   docker compose exec -T -u hermes -e HOME=/opt/data -e UV_LOCK_TIMEOUT=3600 \
#       hermes bash /app/scripts/prime-hindsight-server.sh
#
# Why this is needed
# ------------------
# memory.provider=hindsight in `local_embedded` mode runs `hindsight-api` as its own
# process, in its own uv tool environment - NOT as a Hermes venv dependency (the server
# tree cannot resolve against Hermes' pins and is ~3GB of CUDA-wheels torch). On a
# machine that has never fetched it, the plugin fetches it on first use through
# `uvx hindsight-api@<hindsight_embed.__version__>`.
#
# That fetch does not fit the plugin's start budget on a slow link. The plugin allows
# _DAEMON_START_TIMEOUT = 900s (plugins/hindsight/embedded.py) for it, while ~2.8GB at
# ~2.7MB/s needs ~18 minutes. Worse, uv commits nothing for a download that is killed:
# the partial bytes strand in <cache>/.tmp* and the tool env stays a venv skeleton, so
# every retry restarts from zero. The result is a loop - `Failed to start the Hindsight
# daemon` every few minutes forever, hundreds of MB burned each attempt.
#
# This script does the one thing the plugin cannot: it runs the same install once, with
# no window over it, and holds the uv tools lock for the whole download so the plugin's
# own attempts queue behind it instead of racing it. After it completes, every later
# start is a few seconds and the plugin never fetches again.
#
# No version is written by hand: it comes from the installed client, exactly as
# `daemon_embed_manager` derives its own uvx spec. Idempotent: a no-op once installed,
# so it is safe to leave wired into the boot path.
set -u

PY=/opt/hermes/.venv/bin/python
[ -x "$PY" ] || PY=python3

VERSION=$("$PY" -c 'import hindsight_embed, sys; sys.stdout.write(hindsight_embed.__version__)' 2>/dev/null || true)
if [ -z "${VERSION:-}" ]; then
    echo "hindsight_embed is not importable in $PY - the image is missing the plugin deps."
    echo "That is the Dockerfile.audio layer, not this script. Nothing to prime yet."
    exit 0
fi

HOME_DIR="${HOME:-/opt/data}"
TOOL_ENV="$HOME_DIR/.local/share/uv/tools/hindsight-api"

if [ -x "$TOOL_ENV/bin/hindsight-api" ]; then
    echo "already primed: hindsight-api@$VERSION in $TOOL_ENV ($(du -sh "$TOOL_ENV" 2>/dev/null | cut -f1))"
    exit 0
fi

echo "priming hindsight server hindsight-api@$VERSION (one-off fetch, ~2.8GB on a cold cache)..."
echo "this can take 10-20 minutes; progress goes to this log and the tool env grows in $TOOL_ENV"

# Outlast the plugin's own start attempts rather than losing the lock race to one:
# uv's default wait is 300s and a doomed attempt holds the lock while it downloads.
export UV_LOCK_TIMEOUT="${UV_LOCK_TIMEOUT:-3600}"

if uv tool install "hindsight-api@$VERSION" --no-progress; then
    echo "primed: $(du -sh "$TOOL_ENV" 2>/dev/null | cut -f1) at $TOOL_ENV"
    echo "memory starts in seconds from now on; the plugin will find the binary and never re-fetch."
else
    echo "prime FAILED (see above). Re-run this script; a killed download commits nothing,"
    echo "so it simply starts over - it does not corrupt anything."
    exit 1
fi

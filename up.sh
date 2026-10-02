#!/bin/bash

export HERMES_UID="$(id -u)"
export HERMES_GID="$(id -g)"
docker compose up -d --build

# Prime the Hindsight memory server once, outside the plugin's 900s start window.
# The plugin's own first-boot fetch (~2.8GB) cannot finish inside that window on a slow
# link, and a killed uv download commits nothing, so it retries forever from zero. This
# runs the same install uninterrupted as the service user, so the tool env lands on the
# data volume and survives recreates. Idempotent: a no-op once the server is installed.
# See docs/hindsight-memory.md.
mkdir -p .hermes/logs
docker compose exec -T -u hermes -e HOME=/opt/data -e UV_LOCK_TIMEOUT=3600 hermes \
    bash /app/scripts/prime-hindsight-server.sh >> .hermes/logs/hindsight-prime.log 2>&1 &
echo "hindsight server prime started in the background -> .hermes/logs/hindsight-prime.log"

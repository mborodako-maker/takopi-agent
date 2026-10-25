#!/usr/bin/env bash
set -euo pipefail
cd -- "${TAKOPI_RUNTIME_DIR:-/srv/takopi}"
docker compose pull
docker compose up -d --no-build
docker compose ps

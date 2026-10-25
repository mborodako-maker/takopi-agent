#!/usr/bin/env bash
# Local development setup only; production setup is in README_VPS.md.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
command -v docker >/dev/null
docker compose version >/dev/null
install -d -m 700 config/runtime logs workspace
if [[ ! -f config/runtime/takopi.toml ]]; then
    install -m 600 config/takopi.toml.example config/runtime/takopi.toml
fi
# Run this setup script as the local administrator; application never runs as root.
if [[ $EUID = 0 ]]; then
    chown -R 1001:1001 config/runtime logs workspace
elif [[ $(id -u) != 1001 ]]; then
    echo 'Set owner of config/runtime, logs and workspace to uid/gid 1001 before startup.' >&2
fi

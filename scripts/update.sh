#!/usr/bin/env bash
# Compatibility entry point for production updates.
set -euo pipefail
exec bash "$(dirname -- "${BASH_SOURCE[0]}")/update-vps.sh" "$@"

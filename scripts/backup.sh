#!/usr/bin/env bash
set -euo pipefail
cd -- "${TAKOPI_RUNTIME_DIR:-$(dirname -- "${BASH_SOURCE[0]}")/..}"
: "${BACKUP_DIR:?Set an absolute backup directory outside workspace and this repository}"
[[ $BACKUP_DIR = /* ]]
# Resolve symlinks and '..' before checking the secret-bearing archive location.
BACKUP_DIR=$(realpath -m -- "$BACKUP_DIR")
checkout_root=$(pwd -P)
case "$BACKUP_DIR/" in "$checkout_root/"*) echo 'Backup must be outside the checkout' >&2; exit 1;; esac
umask 077
mkdir -p -- "$BACKUP_DIR"
backup_name="takopi-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
# Stop first for a consistent session/repository snapshot. Never delete volumes.
docker compose stop takopi
trap 'docker compose start takopi' EXIT
docker compose run --rm --no-deps -T takopi tar -czf - \
    -C / home/codex/.codex home/codex/.takopi workspace var/log/takopi \
    > "$BACKUP_DIR/$backup_name"
printf 'Backup saved: %s\n' "$BACKUP_DIR/$backup_name"

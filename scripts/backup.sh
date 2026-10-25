#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
: "${BACKUP_DIR:?Set an absolute backup directory outside workspace and this repository}"
[[ $BACKUP_DIR = /* ]]
case "$BACKUP_DIR/" in "$PWD/"*) echo 'Backup must be outside the checkout' >&2; exit 1;; esac
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

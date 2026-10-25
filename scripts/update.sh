#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
[[ -z $(git status --porcelain --untracked-files=no) ]] || {
    echo 'Tracked changes present; review before updating.' >&2; exit 1;
}
: "${BACKUP_DIR:?Set BACKUP_DIR; a backup is required before update}"
bash scripts/backup.sh
git pull --ff-only
docker compose build --pull
docker compose up -d --no-build

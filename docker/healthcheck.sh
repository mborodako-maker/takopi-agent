#!/bin/sh
set -eu
codex --version >/dev/null 2>&1
python -c '
import os, time
from pathlib import Path
p = Path(os.environ.get("TAKOPI_HEALTH_FILE", "/tmp/takopi-polling"))
assert time.time() - p.stat().st_mtime < 120, "Telegram polling heartbeat expired"
'

"""Admin-only Telegram views; database reads are implemented in trader_reports."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from ...codex_tasks import authorized
from ...trader_reports import COMMANDS, read_report

__all__ = ["COMMANDS", "handle_trader_read"]


async def handle_trader_read(cfg, msg, command, args_text) -> None:
    from .reply import make_reply

    reply = make_reply(cfg, msg)
    if not authorized(msg.sender_id, cfg.allowed_user_ids):
        await reply(text="Administrator access required.")
        return
    if cfg.runtime.config_path is None:
        await reply(text="Persistent config is required.")
        return
    default = (
        cfg.runtime.config_path.resolve().parent / "crypto-trader" / "shadow.sqlite3"
    )
    # Deployment config only. No Telegram argument ever selects a database path.
    path = Path(os.environ.get("TAKOPI_TRADER_DB", str(default)))
    try:
        data = read_report(path, command, args_text)
    except ValueError, OSError, sqlite3.Error:
        await reply(
            text="Trader data unavailable or invalid request. Check service configuration."
        )
        return
    await reply(
        text=(
            "Stored SHADOW/PAPER data (see timestamp):\n"
            + json.dumps(data, ensure_ascii=False)
        )[:3500]
    )

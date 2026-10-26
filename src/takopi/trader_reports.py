"""Read-only views of a separately deployed SHADOW service's persisted results."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import quote

COMMANDS = {"market", "signals", "strategy", "performance", "shadow_status"}


def read_report(path: Path, command: str, args: str) -> dict | list:
    if command not in COMMANDS:
        raise ValueError("unknown read command")
    if command != "market" and args.strip():
        raise ValueError("This command accepts no arguments")
    symbol = args.strip().upper() or "BTC"
    if command == "market":
        symbol = symbol if symbol.endswith("USDT") else symbol + "USDT"
        if not re.fullmatch(r"[A-Z0-9]{2,20}USDT", symbol):
            raise ValueError("Invalid market symbol")
    uri = "file:" + quote(str(path.resolve())) + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=2) as db:
        db.execute("PRAGMA query_only=ON")
        if command == "signals":
            rows = db.execute(
                "SELECT timestamp,symbol,proposal,risk FROM signals ORDER BY id DESC LIMIT 10"
            ).fetchall()
            return [
                {
                    "timestamp": r[0],
                    "symbol": r[1],
                    "decision": json.loads(r[2])["decision"],
                    "risk": json.loads(r[3])["reason"] if r[3] else None,
                }
                for r in rows
            ]
        if command == "market":
            row = db.execute(
                "SELECT snapshot FROM signals WHERE symbol=? ORDER BY id DESC LIMIT 1",
                (symbol,),
            ).fetchone()
            if row is None:
                return {"symbol": symbol, "status": "no stored snapshot"}
            value = json.loads(row[0])
            return {
                key: value.get(key)
                for key in (
                    "symbol",
                    "timestamp",
                    "price",
                    "rsi14",
                    "atr14",
                    "funding_rate",
                    "spread",
                )
            }
        row = db.execute("SELECT value FROM reports WHERE key=?", (command,)).fetchone()
        return json.loads(row[0]) if row else {"status": "no stored report"}

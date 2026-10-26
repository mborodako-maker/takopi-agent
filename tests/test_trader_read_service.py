import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from takopi.trader_reports import read_report


class ReadOnlyReports(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "shadow.sqlite3"
        with sqlite3.connect(self.path) as db:
            db.executescript("""
                CREATE TABLE signals(id INTEGER PRIMARY KEY,timestamp TEXT,symbol TEXT,
                                     proposal TEXT,risk TEXT,snapshot TEXT);
                CREATE TABLE reports(key TEXT PRIMARY KEY,value TEXT);
            """)
            db.execute(
                "INSERT INTO signals VALUES(1,?,?,?,?,?)",
                (
                    "2026-10-26T12:00:00+00:00",
                    "BTCUSDT",
                    json.dumps({"decision": "NO_TRADE"}),
                    None,
                    json.dumps(
                        {"symbol": "BTCUSDT", "timestamp": "fixture", "price": "100"}
                    ),
                ),
            )
            db.execute(
                "INSERT INTO reports VALUES(?,?)",
                ("shadow_status", json.dumps({"mode": "SHADOW"})),
            )

    def test_stored_reports_and_snapshots(self):
        self.assertEqual(
            read_report(self.path, "shadow_status", ""), {"mode": "SHADOW"}
        )
        market = read_report(self.path, "market", "BTC")
        assert isinstance(market, dict)
        self.assertEqual(market["price"], "100")
        signals = read_report(self.path, "signals", "")
        assert isinstance(signals, list)
        self.assertEqual(signals[0]["decision"], "NO_TRADE")
        with sqlite3.connect(self.path) as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM signals").fetchone()[0], 1
            )

    def test_injection_and_extra_args_rejected(self):
        for command, argument in (
            ("market", "BTC;DROP TABLE signals"),
            ("market", "../../db"),
            ("performance", "shell text"),
            ("exec", "id"),
        ):
            with self.subTest(command=command), self.assertRaises(ValueError):
                read_report(self.path, command, argument)

    def test_missing_database_is_not_created(self):
        missing = self.path.with_name("missing.sqlite3")
        with self.assertRaises(sqlite3.Error):
            read_report(missing, "signals", "")
        self.assertFalse(missing.exists())

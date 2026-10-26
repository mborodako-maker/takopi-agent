"""Dependency-light security tests, also runnable with unittest without Telegram."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from takopi.codex_env import safe_codex_env
from takopi.codex_tasks import TaskBusy, TaskService, authorized, list_tasks, read_task


class TaskFiles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "codex_tasks").mkdir()
        (self.root / "codex_tasks" / "safe.md").write_text("Run the tests.")

    def test_predefined_files_only(self):
        self.assertEqual(list_tasks(self.root), ["safe.md"])
        self.assertEqual(read_task(self.root, "safe.md"), "Run the tests.")

    def test_attack_names(self):
        for name in (
            "../safe.md",
            "/tmp/safe.md",
            "C:\\safe.md",
            "safe.md;id",
            "$(id).md",
            "`id`.md",
            "a/b.md",
            "safe.MD",
            "a.md\n",
            "--safe.md",
            "a..md",
            "safe.md | id",
        ):
            with self.subTest(name=name), self.assertRaises((ValueError, OSError)):
                read_task(self.root, name)

    def test_symlink_file_escape(self):
        (self.root / "outside.md").write_text("outside")
        (self.root / "codex_tasks" / "link.md").symlink_to(self.root / "outside.md")
        self.assertEqual(list_tasks(self.root), ["safe.md"])
        with self.assertRaises(OSError):
            read_task(self.root, "link.md")

    def test_symlink_directory_escape(self):
        other = self.root / "other"
        other.mkdir()
        (other / "safe.md").write_text("outside")
        (self.root / "codex_tasks" / "safe.md").unlink()
        (self.root / "codex_tasks").rmdir()
        (self.root / "codex_tasks").symlink_to(other)
        with self.assertRaises(ValueError):
            read_task(self.root, "safe.md")

    def test_non_regular_and_oversized_files(self):
        (self.root / "codex_tasks" / "directory.md").mkdir()
        (self.root / "codex_tasks" / "huge.md").write_bytes(b"x" * (256 * 1024 + 1))
        self.assertEqual(list_tasks(self.root), ["safe.md"])

    def test_admin_whitelist(self):
        self.assertTrue(authorized(42, (42,)))
        self.assertFalse(authorized(None, (42,)))
        self.assertFalse(authorized(99, (42,)))
        self.assertFalse(authorized(42, ()))

    def test_environment_allowlist(self):
        parent = dict.fromkeys(
            (
                "BYBIT_API_KEY",
                "BYBIT_API_SECRET",
                "TELEGRAM_BOT_TOKEN",
                "DATABASE_PASSWORD",
                "CUSTOM_CREDENTIAL",
                "OPENAI_API_KEY",
                "PYTHONPATH",
                "LD_PRELOAD",
                "HTTPS_PROXY",
            ),
            "secret",
        )
        parent.update(
            HOME="/home/worker",
            PATH="/usr/bin",
            USER="worker",
            CODEX_HOME="/state/codex",
            TAKOPI_APPROVAL_SOCKET="/tmp/gateway.sock",
        )
        self.assertEqual(
            set(safe_codex_env(parent)),
            {"HOME", "PATH", "USER", "CODEX_HOME", "TAKOPI_APPROVAL_SOCKET"},
        )


class Lifecycle(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        (self.project / "codex_tasks").mkdir(parents=True)
        (self.project / "codex_tasks" / "safe.md").write_text("Test content")
        self.service = TaskService(self.root / "state")
        self.acknowledgements = []

    async def ack(self, record):
        self.acknowledgements.append(record["task_name"])

    async def execute(self, text):
        self.assertEqual(text, "Test content")
        return 0, "Changed files: a.py\nTests: passed\nsecret", ""

    async def run_service(self, **kw):
        return await self.service.run(
            self.project,
            "safe.md",
            kw.pop("user", 42),
            (42,),
            kw.pop("execute", self.execute),
            self.ack,
            lambda text: text.replace("secret", "[redacted]"),
        )

    async def test_logs_metadata_and_immediate_ack(self):
        record = await self.run_service()
        self.assertEqual(self.acknowledgements, ["safe.md"])
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["exit_code"], 0)
        self.assertIsNotNone(record["finished_at"])
        self.assertIn("[redacted]", Path(record["stdout_log_path"]).read_text())
        self.assertNotIn("secret", Path(record["stdout_log_path"]).read_text())
        self.assertTrue(Path(record["stderr_log_path"]).is_file())
        self.assertEqual(
            json.loads((self.service.state_dir / "status.json").read_text()), record
        )
        self.assertEqual(self.service.status()["status"], "completed")

    async def test_unauthorized_never_executes(self):
        with self.assertRaises(PermissionError):
            await self.run_service(user=999)
        self.assertFalse(self.acknowledgements)
        self.assertFalse(self.service.state_dir.exists())

    async def test_concurrency_across_service_instances(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def wait(text):
            entered.set()
            await release.wait()
            return 0, "", ""

        first = asyncio.create_task(self.run_service(execute=wait))
        await entered.wait()
        other = TaskService(self.service.state_dir)
        self.assertEqual(other.status()["status"], "running")
        with self.assertRaisesRegex(TaskBusy, "safe.md"):
            await other.run(
                self.project, "safe.md", 42, (42,), self.execute, self.ack, str
            )
        release.set()
        await first

    async def test_execution_failure_does_not_log_exception_secrets(self):
        async def failed(text):
            raise RuntimeError("secret")

        record = await self.run_service(execute=failed)
        self.assertEqual(record["status"], "failed")
        self.assertNotIn("secret", (self.service.state_dir / "status.json").read_text())

    async def test_timeout_waits_for_worker_cleanup_before_unlock(self):
        self.service.timeout = 0.02
        cleaned = []

        async def wait(text):
            try:
                await asyncio.sleep(10)
            finally:
                cleaned.append(True)

        record = await self.run_service(execute=wait)
        self.assertTrue(cleaned)
        self.assertEqual(record["exit_code"], 124)
        self.assertEqual(record["status"], "timed_out")
        self.assertEqual((await self.run_service())["status"], "completed")

    async def test_cancellation_is_persisted(self):
        entered = asyncio.Event()

        async def wait(text):
            entered.set()
            await asyncio.sleep(10)

        task = asyncio.create_task(self.run_service(execute=wait))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.service.status()["status"], "cancelled")

    async def test_stale_running_record_marked_interrupted(self):
        record = await self.run_service()
        record["status"] = "running"
        self.service._save(record)
        self.assertEqual(self.service.status()["status"], "interrupted")

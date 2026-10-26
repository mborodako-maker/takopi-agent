"""Predefined maintenance tasks; no shell grammar or production execution API.

The Telegram adapter supplies the existing, approval-gated Codex app-server
runner. This module deliberately knows nothing about exchange credentials.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import re
import stat
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

TASK_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,119}\.md\Z")
MAX_TASK_BYTES = 256 * 1024


def authorized(user_id: int | None, allowed: tuple[int, ...]) -> bool:
    return user_id is not None and user_id in allowed


def read_task(project: Path, name: str) -> str:
    if not TASK_NAME.fullmatch(name):
        raise ValueError("Only a predefined .md filename is allowed")
    base = project.resolve(strict=True)
    directory = base / "codex_tasks"
    if directory.is_symlink() or not directory.resolve(strict=True).is_relative_to(
        base
    ):
        raise ValueError("Task directory must be physically inside the project")
    # Open relative to the directory descriptor and reject symlinks, including
    # replacement races between a path check and opening the actual file.
    dfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
        with os.fdopen(fd, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_TASK_BYTES:
                raise ValueError("Task must be a bounded regular file")
            raw = stream.read(MAX_TASK_BYTES + 1)
    finally:
        os.close(dfd)
    if len(raw) > MAX_TASK_BYTES:
        raise ValueError("Task is too large")
    text = raw.decode("utf-8")
    if not text.strip():
        raise ValueError("Task is empty")
    return text


def list_tasks(project: Path) -> list[str]:
    directory = project / "codex_tasks"
    if not directory.exists():
        return []
    result = []
    for path in directory.iterdir():
        try:
            read_task(project, path.name)
        except ValueError, OSError, UnicodeError:
            continue
        result.append(path.name)
    return sorted(result)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class TaskBusy(ValueError):
    pass


class TaskService:
    """One task per bot state directory, also across OS processes/worktrees.

    State belongs outside repositories so a model cannot rewrite it. flock
    releases after crashes. Persisted 'running' becomes interrupted on status
    after a restart; no child is ever automatically resumed.
    """

    def __init__(self, state_dir: Path, *, timeout: float = 3600) -> None:
        self.state_dir = state_dir
        self.timeout = timeout

    def _lock(self) -> int:
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(
            self.state_dir / "runner.lock",
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
            0o600,
        )
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise TaskBusy("Codex task already running") from None
        return fd

    def _save(self, record: dict) -> None:
        encoded = json.dumps(record, ensure_ascii=False, indent=2)
        for path in (
            self.state_dir / f"{record['task_id']}.json",
            self.state_dir / "status.json",
        ):
            temp = path.with_suffix(".tmp")
            fd = os.open(
                temp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600
            )
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(encoded)
            temp.replace(path)

    def status(self) -> dict:
        try:
            record = json.loads((self.state_dir / "status.json").read_text())
        except FileNotFoundError:
            return {"status": "idle"}
        if record["status"] == "running":
            try:
                fd = self._lock()
            except TaskBusy:
                return record
            try:
                # Re-read under the lock: another task may have finished.
                record = json.loads((self.state_dir / "status.json").read_text())
                if record["status"] == "running":
                    record.update(status="interrupted", finished_at=_now())
                    self._save(record)
            finally:
                os.close(fd)
        return record

    async def run(
        self,
        project: Path,
        name: str,
        user_id: int | None,
        allowed: tuple[int, ...],
        execute: Callable[[str], Awaitable[tuple[int, str, str]]],
        acknowledge: Callable[[dict], Awaitable[None]],
        redact: Callable[[str], str],
    ) -> dict:
        if not authorized(user_id, allowed):
            raise PermissionError("Administrator access required")
        text = read_task(project, name)
        try:
            fd = self._lock()
        except TaskBusy:
            running = self.status()
            raise TaskBusy(
                f"Codex task already running: {running.get('task_name', 'unknown')}"
            ) from None
        task_id = uuid.uuid4().hex
        stdout = self.state_dir / f"{task_id}.stdout.log"
        stderr = self.state_dir / f"{task_id}.stderr.log"
        record = {
            "task_id": task_id,
            "task_name": name,
            "task_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "started_at": _now(),
            "finished_at": None,
            "telegram_user_id": user_id,
            "exit_code": None,
            "stdout_log_path": str(stdout),
            "stderr_log_path": str(stderr),
            "status": "running",
            "project": str(project.resolve()),
        }
        try:
            for path in (stdout, stderr):
                lfd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(lfd)
            self._save(record)
            try:
                await acknowledge(record)
                async with asyncio.timeout(self.timeout):
                    code, output, error = await execute(text)
                record.update(
                    status="completed" if code == 0 else "failed", exit_code=code
                )
                # Only bounded, redacted final output is persisted; no raw tool
                # events, environment, prompts or exception text are written.
                for path, value in ((stdout, output), (stderr, error)):
                    lfd = os.open(path, os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                    with os.fdopen(lfd, "w", encoding="utf-8") as log:
                        log.write(redact(value)[:65536])
            except TimeoutError:
                record.update(status="timed_out", exit_code=124)
            except asyncio.CancelledError:
                record.update(status="cancelled")
                raise
            except Exception:  # noqa: BLE001 - never persist execution exception values
                record.update(status="failed", exit_code=1)
        finally:
            record["finished_at"] = _now()
            try:
                self._save(record)
            finally:
                os.close(fd)
        return record

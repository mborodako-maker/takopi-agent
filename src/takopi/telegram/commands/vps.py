"""VPS workflow commands; use Takopi's context/worktree and interrupt controls."""

from __future__ import annotations

import hashlib
import os
import shlex
import time
from pathlib import Path
from typing import TYPE_CHECKING

import anyio

from ...context import RunContext
from ...logging import _redact_text
from ...security import (
    ApprovalScope,
    Classification,
    classify_command,
    sensitive_path,
    resolve_project_path,
)
from ...utils.subprocess import manage_subprocess
from .reply import make_reply

if TYPE_CHECKING:
    from ...runner_bridge import RunningTasks
    from ..bridge import TelegramBridgeConfig
    from ..types import TelegramIncomingMessage

COMMANDS = {"projects", "status", "abort", "diff", "tests", "commit"}


def execution_env() -> dict[str, str]:
    # Deliberately do not inherit application credentials or arbitrary env secrets.
    keys = {"PATH", "HOME", "CODEX_HOME", "TZ", "LANG", "LC_ALL", "TMPDIR"}
    return {key: value for key, value in os.environ.items() if key in keys}


async def run_bounded(
    argv: list[str], cwd: Path, *, timeout: float = 120, max_bytes: int = 1024 * 1024
) -> tuple[int, str]:
    chunks = bytearray()
    async with manage_subprocess(argv, cwd=cwd, env=execution_env(), stderr=-2) as proc:
        assert proc.stdout is not None
        with anyio.move_on_after(timeout) as scope:
            async for chunk in proc.stdout:
                if len(chunks) + len(chunk) > max_bytes:
                    return 1, "Output limit exceeded."
                chunks.extend(chunk)
            rc = await proc.wait()
        if scope.cancel_called:
            return 124, "Command timed out."
    return rc, _redact_text(chunks.decode(errors="replace"))


async def git(root: Path, *args: str) -> tuple[int, str]:
    return await run_bounded(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.pager=cat",
            "-c",
            "core.fsmonitor=false",
            *args,
        ],
        root,
    )


async def _handle_vps_command(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    command: str,
    args_text: str,
    ambient: RunContext | None,
    running_tasks: RunningTasks,
) -> None:
    reply = make_reply(cfg, msg)
    if command == "projects":
        aliases = cfg.runtime.project_aliases()
        await reply(text="Projects:\n" + "\n".join("/" + alias for alias in aliases))
        return
    gateway = cfg.approval_gateway
    saved = (
        gateway.last_context(msg.chat_id, msg.thread_id, msg.sender_id)
        if gateway
        else None
    )
    context = saved or ambient
    resolved = cfg.runtime.resolve_message(
        text=args_text,
        reply_text=msg.reply_to_text,
        ambient_context=context,
        chat_id=msg.chat_id,
    )
    context = resolved.context
    if context is None or context.project is None:
        await reply(
            text="Select a project with /project @branch, or reply to its progress message."
        )
        return
    root = cfg.runtime.resolve_run_cwd(context)
    if root is None:
        await reply(text="Project/worktree unavailable.")
        return
    # Restrict controls to the selected chat/topic/worktree.
    active = [
        (ref, task)
        for ref, task in running_tasks.items()
        if ref.channel_id == msg.chat_id
        and ref.thread_id == msg.thread_id
        and task.context == context
    ]
    _, branch = await git(root, "branch", "--show-current")
    if command == "status":
        session = (
            gateway.last_session(msg.chat_id, msg.thread_id, msg.sender_id)
            if gateway
            else None
        )
        lines = [
            f"Project: {context.project}",
            f"Branch: {branch.strip()}",
            f"Worktree: {root}",
            f"Codex session: {session or 'none'}",
            f"Turn running: {bool(active)}",
        ]
        if active:
            lines.append(f"Elapsed: {time.monotonic() - active[0][1].started_at:.0f}s")
        await reply(text="\n".join(lines))
        return
    if command == "abort":
        for _, task in active:
            task.cancel_requested.set()
        await reply(
            text="Interrupt requested."
            if active
            else "No active turn in this worktree."
        )
        return
    if command == "tests":
        project = cfg.runtime.project_config(context.project)
        configured = project.test_command if project else None
        if not configured:
            await reply(text="Set projects.<alias>.test_command in application config.")
            return
        if resolved.prompt.strip():
            await reply(
                text="/tests accepts project directives only; command comes from config."
            )
            return
        if classify_command(configured, root=root) != Classification.AUTO_ALLOW:
            await reply(text="Configured test command is outside the safe test policy.")
            return
        argv = shlex.split(configured)
        test_tools = {"pytest", "npm", "composer", "dotnet", "mvn", "gradle"}
        if not argv or argv[0] not in test_tools:
            await reply(
                text="Configured command must be an explicitly supported test/lint command."
            )
            return
        await reply(text=f"Running configured tests: {configured}")
        # The official Codex sandbox executes the exact configured argv, without
        # asking the model to interpret it. Never fall back to unsandboxed tests.
        rc, output = await run_bounded(
            [
                "codex",
                "sandbox",
                "-c",
                'sandbox_mode="workspace-write"',
                "-c",
                "sandbox_workspace_write.network_access=false",
                "--",
                *argv,
            ],
            root,
            timeout=300,
        )
        if gateway:
            gateway.audit.write(
                "execution.result",
                user_id=msg.sender_id,
                project=context.project,
                worktree=root,
                requested_command=configured,
                execution_result=rc,
            )
        await reply(text=f"Tests exit code: {rc}\n{output[:3000]}")
        return
    if command == "diff":
        rc, raw_names = await git(root, "diff", "--name-only", "-z", "HEAD")
        if rc:
            await reply(text="Git diff failed (check project has an initial commit).")
            return
        paths = []
        for name in raw_names.split("\0"):
            if not name:
                continue
            try:
                resolve_project_path(root, name)
                paths.append(name)
            except (ValueError, OSError):
                continue
        summary, diff = "", ""
        if paths:
            _, summary = await git(
                root,
                "--literal-pathspecs",
                "diff",
                "--stat",
                "--no-ext-diff",
                "--no-textconv",
                "HEAD",
                "--",
                *paths,
            )
            _, diff = await git(
                root,
                "--literal-pathspecs",
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "HEAD",
                "--",
                *paths,
            )
        _, names = await git(root, "ls-files", "--others", "--exclude-standard", "-z")
        untracked = []
        for name in names.split("\0"):
            if not name:
                continue
            try:
                target = resolve_project_path(root, name)
                if target.is_symlink() or not target.is_file():
                    continue
                untracked.append(name)
                if target.stat().st_size < 64 * 1024:
                    _, new_diff = await git(
                        root,
                        "diff",
                        "--no-index",
                        "--no-ext-diff",
                        "--no-textconv",
                        "--",
                        "/dev/null",
                        str(target),
                    )
                    diff += new_diff
            except (ValueError, OSError):
                continue
        text = summary + ("\nUntracked: " + ", ".join(untracked) if untracked else "")
        await reply(text=text[:3000] or "No changes.")
        if len(diff) <= 3000:
            if diff:
                await reply(text=diff)
        elif len(diff.encode()) <= cfg.files.max_file_bytes:
            await cfg.bot.send_document(
                chat_id=msg.chat_id,
                filename="changes.diff",
                content=diff.encode(),
                message_thread_id=msg.thread_id,
            )
        else:
            await reply(text="Diff exceeds the configured transfer size limit.")
        return
    if command == "commit":
        if gateway is None or msg.sender_id is None:
            await reply(text="Approval gateway unavailable.")
            return
        if active:
            await reply(text="Wait for the active Codex turn before committing.")
            return
        _, staged = await git(root, "diff", "--cached", "--name-only", "-z")
        names = [n for n in staged.split("\0") if n]
        if not names:
            await reply(
                text="No staged files. Review and stage the intended files first; /commit never stages everything."
            )
            return
        if any(sensitive_path(Path(n)) for n in names):
            await reply(text="⛔ Sensitive staged path blocked.")
            return
        _, before_head = await git(root, "rev-parse", "HEAD")
        _, before = await git(
            root, "diff", "--cached", "--binary", "--no-ext-diff", "--no-textconv"
        )
        message = f"chore: update {context.project}"
        await reply(
            text=f"Commit preview\nBranch: {branch.strip()}\nFiles:\n"
            + "\n".join(names)
            + f"\nMessage: {message}"
        )
        turn_id = "commit:" + str(msg.message_id)
        accepted = await gateway.request(
            scope=ApprovalScope(
                msg.sender_id,
                msg.chat_id,
                root,
                context.project,
                branch.strip(),
                msg.thread_id,
            ),
            thread_id="telegram",
            turn_id=turn_id,
            request_id=turn_id,
            command="git commit",
            classification=Classification.REQUIRE_APPROVAL,
        )
        _, after_branch = await git(root, "branch", "--show-current")
        _, after_head = await git(root, "rev-parse", "HEAD")
        _, after = await git(
            root, "diff", "--cached", "--binary", "--no-ext-diff", "--no-textconv"
        )
        if not accepted:
            await reply(text="Commit denied or expired.")
        elif (
            branch != after_branch
            or before_head != after_head
            or hashlib.sha256(before.encode()).digest()
            != hashlib.sha256(after.encode()).digest()
        ):
            await reply(text="Index changed; request a new commit preview.")
        else:
            rc, output = await git(
                root, "-c", "commit.gpgsign=false", "commit", "-m", message
            )
            gateway.audit.write(
                "execution.result",
                user_id=msg.sender_id,
                project=context.project,
                worktree=root,
                turn_id=turn_id,
                requested_command="git commit",
                execution_result=rc,
            )
            await reply(text=f"Commit exit code: {rc}\n{output[:1500]}")


async def handle_vps_command(
    cfg, msg, command, args_text, ambient, running_tasks
) -> None:
    try:
        await _handle_vps_command(cfg, msg, command, args_text, ambient, running_tasks)
    except Exception as exc:  # noqa: BLE001
        from ...logging import get_logger

        get_logger(__name__).warning(
            "vps.command_failed", command=command, error_type=type(exc).__name__
        )
        await make_reply(cfg, msg)(
            text="Command failed safely. See application logs for the error type."
        )

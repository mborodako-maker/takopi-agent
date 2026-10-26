"""Admin-only maintenance tasks using the existing sandbox/approval gateway."""

from __future__ import annotations

from pathlib import Path

from ...codex_tasks import TaskBusy, TaskService, authorized, list_tasks
from ...logging import _redact_text
from ...model import ActionEvent, CompletedEvent
from ...runners.codex import AppServerCodexRunner
from ...utils.paths import reset_run_base_dir, set_run_base_dir
from .reply import make_reply

COMMANDS = {"codex_task", "trader_upgrade"}
UPGRADE_TASK = "CODEX_CRYPTO_TRADER_SKILLS_TASK.md"


async def handle_codex_task(cfg, msg, command, args_text, ambient) -> None:
    reply = make_reply(cfg, msg)
    if not authorized(msg.sender_id, cfg.allowed_user_ids):
        await reply(text="Administrator access required.")
        return
    config = cfg.runtime.config_path
    gateway = cfg.approval_gateway
    if config is None or gateway is None:
        await reply(text="Task runner requires persistent config and approval gateway.")
        return
    service = TaskService(config.resolve().parent / "logs" / "codex_tasks")
    args = args_text.split()
    if command == "trader_upgrade":
        if args:
            await reply(text="/trader_upgrade accepts no arguments.")
            return
        args = ["run", UPGRADE_TASK]
    if args == ["status"]:
        record = service.status()
        await reply(
            text=f"Codex task: {record.get('task_name', 'none')}\n"
            f"Status: {record['status']}\nExit code: {record.get('exit_code')}"
        )
        return
    if args != ["list"] and (len(args) != 2 or args[0] != "run"):
        await reply(text="Usage: /codex_task list | run <filename.md> | status")
        return
    context = gateway.last_context(msg.chat_id, msg.thread_id, msg.sender_id) or ambient
    resolved = cfg.runtime.resolve_message(
        text="",
        reply_text=msg.reply_to_text,
        ambient_context=context,
        chat_id=msg.chat_id,
    )
    context = resolved.context
    if context is None or context.project is None:
        await reply(text="Select a configured project before listing/running tasks.")
        return
    try:
        root = cfg.runtime.resolve_run_cwd(context)
        if root is None:
            raise ValueError("Project/worktree unavailable")
        if service.state_dir.resolve().is_relative_to(root.resolve()):
            raise ValueError("Task state/log directory must be outside the project")
        if args == ["list"]:
            names = list_tasks(root)
            await reply(text="Codex tasks:\n" + ("\n".join(names) or "none"))
            return
        entry = cfg.runtime.resolve_runner(resume_token=None, engine_override="codex")
        configured = entry.runner
        if not entry.available or not isinstance(configured, AppServerCodexRunner):
            raise ValueError("Maintenance tasks require codex.mode = app_server")

        async def execute(text: str) -> tuple[int, str, str]:
            # Own this process: timeout/shutdown must terminate the worker before
            # releasing the task lock, without interrupting conversational turns.
            runner = AppServerCodexRunner(
                codex_cmd=configured.codex_cmd, extra_args=list(configured.extra_args)
            )
            token = set_run_base_dir(root)
            try:
                with gateway.bind_run(msg.chat_id, msg.message_id, root, context):
                    answer, error, code = "", "No completed turn received", 1
                    changes: set[str] = set()
                    async for event in runner.run(
                        "Maintenance only. Do not place, modify or cancel orders, "
                        "enable trading, read secrets or call production executors.\n\n"
                        + text,
                        None,
                    ):
                        if (
                            isinstance(event, ActionEvent)
                            and event.action.kind == "file_change"
                        ):
                            for change in event.action.detail.get("changes", []):
                                if isinstance(change, dict) and isinstance(
                                    change.get("path"), str
                                ):
                                    changes.add(change["path"])
                        if isinstance(event, CompletedEvent):
                            code, answer, error = (
                                (0 if event.ok else 1),
                                event.answer,
                                event.error or "",
                            )
                    report = "Changed files: " + (
                        ", ".join(sorted(changes)) or "none observed"
                    )
                    return code, report + "\n" + answer, error
            finally:
                try:
                    await runner.aclose()
                finally:
                    reset_run_base_dir(token)

        async def acknowledge(record: dict) -> None:
            await reply(
                text=f"Started Codex task:\n{record['task_name']}\nID: {record['task_id']}"
            )

        record = await service.run(
            root,
            args[1],
            msg.sender_id,
            cfg.allowed_user_ids,
            execute,
            acknowledge,
            _redact_text,
        )
        log = Path(record["stdout_log_path"])
        final = log.read_text(encoding="utf-8") if log.is_file() else "No final report."
        await reply(
            text=f"Task {record['status']}\nExit code: {record['exit_code']}\n"
            f"Log: {log}\n{final[:2500]}"
        )
    except (ValueError, OSError, UnicodeError) as exc:
        # Filenames and configuration failures contain no task/exception output.
        message = (
            str(exc)
            if isinstance(exc, TaskBusy)
            else "Invalid task or runner configuration."
        )
        await reply(text=message)

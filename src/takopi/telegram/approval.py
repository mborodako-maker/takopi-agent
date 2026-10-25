"""Telegram UI and Codex hook transport for the shared security policy."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import anyio

from ..context import RunContext
from ..utils.json_state import atomic_write_json
from ..logging import get_logger
from ..security import (
    ApprovalScope,
    ApprovalStore,
    AuditLog,
    Classification,
    CURRENT_SCOPE,
    classify_command,
    command_preview,
    resolve_project_path,
)
from .client_api import BotClient

logger = get_logger(__name__)


class TelegramApprovalGateway:
    def __init__(
        self,
        bot: BotClient,
        *,
        audit_path: Path | None,
        ttl: float = 120,
        state_path: Path | None = None,
    ) -> None:
        self.state_path = state_path
        self.contexts: dict[str, Any] = {}
        if state_path is not None and state_path.exists():
            self.contexts = json.loads(state_path.read_text())
        self.bot = bot
        self.store = ApprovalStore(ttl=ttl)
        self.audit = AuditLog(audit_path)
        self.origins: OrderedDict[tuple[int, int], tuple[int, int | None]] = (
            OrderedDict()
        )
        self.turns: dict[tuple[str, str], ApprovalScope] = {}
        self._server: asyncio.Server | None = None

    @staticmethod
    def context_key(chat_id, topic_id, user_id) -> str:
        return f"{chat_id}:{topic_id}:{user_id}"

    def last_context(self, chat_id, topic_id, user_id) -> RunContext | None:
        record = self.contexts.get(self.context_key(chat_id, topic_id, user_id))
        if record:
            return RunContext(project=record["project"], branch=record.get("branch"))
        return None

    def last_session(self, chat_id, topic_id, user_id) -> str | None:
        record = self.contexts.get(self.context_key(chat_id, topic_id, user_id), {})
        return record.get("session")

    def register_turn(self, thread_id: str, turn_id: str, scope: ApprovalScope) -> None:
        self.turns[(thread_id, turn_id)] = scope
        key = self.context_key(scope.chat_id, scope.topic_id, scope.user_id)
        self.contexts[key] = {
            "project": scope.project,
            "branch": scope.branch,
            "session": thread_id,
        }
        if self.state_path:
            atomic_write_json(self.state_path, self.contexts)

    def remember(
        self, chat_id: int, message_id: int, user_id: int, topic_id: int | None
    ) -> None:
        self.origins[(chat_id, message_id)] = (user_id, topic_id)
        if len(self.origins) > 10000:
            self.origins.popitem(last=False)

    @contextmanager
    def bind_run(
        self, chat_id: int, message_id: int, root: Path, context: RunContext | None
    ):
        origin = self.origins.get((chat_id, message_id))
        scope = (
            ApprovalScope(
                origin[0],
                chat_id,
                root.resolve(),
                context.project or "unknown" if context else "unknown",
                context.branch if context else None,
                origin[1],
            )
            if origin
            else None
        )
        token = CURRENT_SCOPE.set(scope)
        try:
            yield
        finally:
            CURRENT_SCOPE.reset(token)

    def close_turn(self, thread_id: str, turn_id: str) -> None:
        self.turns.pop((thread_id, turn_id), None)
        self.store.invalidate_turn(thread_id, turn_id)

    async def request(
        self,
        *,
        scope: ApprovalScope,
        thread_id: str,
        turn_id: str,
        request_id: str,
        command: str,
        classification: Classification,
    ) -> bool:
        fields = dict(  # noqa: C408
            user_id=scope.user_id,
            project=scope.project,
            worktree=str(scope.root),
            session=thread_id,
            turn_id=turn_id,
            request_id=request_id,
            requested_command=command_preview(command),
            command_sha256=hashlib.sha256(command.encode()).hexdigest(),
            classification=classification,
        )
        self.audit.write("operation.classified", **fields)
        if classification == Classification.AUTO_ALLOW:
            self.audit.write(
                "approval.result",
                approval_requested=False,
                approval_result="auto_allow",
                **fields,
            )
            return True
        if classification == Classification.DENY:
            self.audit.write(
                "approval.result",
                approval_requested=False,
                approval_result="deny",
                **fields,
            )
            with anyio.move_on_after(5):
                await self.bot.send_message(
                    chat_id=scope.chat_id,
                    message_thread_id=scope.topic_id,
                    text="⛔ Операция заблокирована политикой безопасности.\n"
                    + command_preview(command),
                )
            return False
        token, pending = self.store.create(
            scope, thread_id, turn_id, request_id, command
        )
        try:
            self.audit.write("approval.requested", approval_requested=True, **fields)
            with anyio.move_on_after(self.store.ttl):
                sent = await self.bot.send_message(
                    chat_id=scope.chat_id,
                    message_thread_id=scope.topic_id,
                    text=(
                        "⚠️ Codex запрашивает выполнение команды\n\n"
                        f"Project: {scope.project}\nBranch/worktree: {scope.branch or 'current'} / {scope.root}\n"
                        f"Session: {thread_id}\nTurn: {turn_id}\nTTL: {self.store.ttl:g}s\n\n"
                        f"Command:\n{command_preview(command)}\nSHA256: {pending.command_digest[:16]}"
                    ),
                    reply_markup={
                        "inline_keyboard": [
                            [
                                {
                                    "text": "✅ Разрешить",
                                    "callback_data": f"approval:{token}:yes",
                                },
                                {
                                    "text": "❌ Запретить",
                                    "callback_data": f"approval:{token}:no",
                                },
                            ]
                        ]
                    },
                )
                if sent is None:
                    pending.result = "delivery_failed"
                else:
                    await pending.event.wait()
            if pending.result == "pending":
                pending.result = "expired"
            self.audit.write(
                "approval.result",
                approval_requested=True,
                approval_result=pending.result,
                **fields,
            )
            logger.info(
                "approval.resolved",
                user_id=scope.user_id,
                turn_id=turn_id,
                result=pending.result,
            )
            return pending.accepted and self.store.clock() < pending.expires_at
        finally:
            self.store.pending.pop(token, None)

    async def callback(self, query: Any) -> None:
        parts = (query.data or "").split(":")
        ok = (
            len(parts) == 3
            and parts[2] in {"yes", "no"}
            and self.store.decide(
                parts[1],
                user_id=query.sender_id,
                chat_id=query.chat_id,
                accept=parts[2] == "yes",
            )
        )
        await self.bot.answer_callback_query(
            query.callback_query_id,
            text="Решение принято."
            if ok
            else "Approval недействителен, истёк или принадлежит другому пользователю.",
        )

    async def start_hook_server(self, path: Path) -> None:
        # A private local socket, never a published TCP listener.
        path.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(
            self._hook_connection, path=str(path), limit=1024 * 1024
        )
        path.chmod(0o600)

    async def stop(self) -> None:
        for thread_id, turn_id in list(self.turns):
            self.close_turn(thread_id, turn_id)
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _hook_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        allowed = False
        try:
            with anyio.fail_after(5):
                data = json.loads(await reader.readline())
            thread_id, turn_id = (
                str(data.get("session_id", "")),
                str(data.get("turn_id", "")),
            )
            scope = self.turns.get((thread_id, turn_id))
            if scope is not None:
                tool = data.get("tool_name")
                args = data.get("tool_input") or {}
                command = (
                    str(args.get("command") or args.get("cmd") or "")
                    if isinstance(args, dict)
                    else str(args)
                )
                if data.get("hook_event_name") == "PostToolUse":
                    self.audit.write(
                        "execution.result",
                        user_id=scope.user_id,
                        project=scope.project,
                        worktree=scope.root,
                        session=thread_id,
                        turn_id=turn_id,
                        requested_command=command_preview(command),
                        execution_result="tool_completed",
                    )
                    allowed = True
                else:
                    classification = Classification.DENY
                    if tool in {"Bash", "exec_command", "shell_command", "shell"}:
                        cwd = Path(
                            args.get("workdir")
                            or args.get("cwd")
                            or data.get("cwd")
                            or scope.root
                        )
                        classification = classify_command(
                            command, root=scope.root, cwd=cwd
                        )
                    elif tool == "apply_patch":
                        classification = classify_patch(command, scope.root)
                    elif tool in {"update_plan"}:
                        classification = Classification.AUTO_ALLOW
                    # Unknown tools are blocked: interpreters, MCP and subagents
                    # must not create an unchecked execution route.
                    allowed = await self.request(
                        scope=scope,
                        thread_id=thread_id,
                        turn_id=turn_id,
                        request_id="hook:"
                        + str(data.get("tool_use_id") or uuid.uuid4()),
                        command=command or str(tool),
                        classification=classification,
                    )
                    allowed = allowed and (thread_id, turn_id) in self.turns
        except Exception as exc:  # noqa: BLE001
            logger.warning("approval.hook_failed", error_type=type(exc).__name__)
        finally:
            writer.write(json.dumps({"allow": allowed}).encode() + b"\n")
            await writer.drain()
            writer.close()
            await writer.wait_closed()


def classify_patch(patch: str, root: Path) -> Classification:
    paths = []
    for line in patch.splitlines():
        for prefix in (
            "*** Add File: ",
            "*** Update File: ",
            "*** Delete File: ",
            "*** Move to: ",
        ):
            if line.startswith(prefix):
                paths.append(line[len(prefix) :])  # noqa: PERF401
    if not paths:
        return Classification.DENY
    try:
        for value in paths:
            resolve_project_path(root, value)
    except (ValueError, OSError, RuntimeError):
        return Classification.DENY
    if len(paths) > 20 or "*** Delete File: " in patch:
        return Classification.REQUIRE_APPROVAL
    return Classification.AUTO_ALLOW

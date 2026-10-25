"""Conservative policy and ephemeral, single-use approval capabilities.

This module never executes commands. Codex's sandbox remains an independent
boundary; approving a request must not grant a persistent policy amendment.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shlex
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import StrEnum
from pathlib import Path
from typing import Any

import anyio

from .logging import _redact_text, _redact_value


class Classification(StrEnum):
    AUTO_ALLOW = "AUTO_ALLOW"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    DENY = "DENY"


SYSTEM_ROOTS = ("/etc", "/root", "/proc", "/sys", "/dev")
FORBIDDEN_COMMANDS = {
    "sudo",
    "su",
    "mount",
    "umount",
    "iptables",
    "ip6tables",
    "nft",
    "nftables",
    "reboot",
    "shutdown",
    "poweroff",
    "useradd",
    "userdel",
    "usermod",
    "groupadd",
    "groupdel",
    "passwd",
    "chpasswd",
    "visudo",
}
SECRET_PART = re.compile(r"credentials|secret", re.I)


def sensitive_path(path: Path) -> bool:
    for part in path.parts:
        lowered = part.lower()
        if (
            lowered in {".git", ".ssh", ".codex", ".takopi", "auth.json", ".envrc"}
            or lowered == ".env"
            or lowered.startswith(".env.")
            or lowered.endswith((".pem", ".key"))
            or lowered in {"id_rsa", "id_ed25519", "id_dsa", "id_ecdsa"}
            or SECRET_PART.search(part)
        ):
            return True
    return False


def resolve_project_path(
    root: Path, value: str | Path, *, protect_secrets: bool = True
) -> Path:
    base = root.resolve(strict=True)
    raw = Path(value)
    if protect_secrets and sensitive_path(raw):
        raise ValueError("sensitive path")
    target = (base / raw).resolve(strict=False)
    if not target.is_relative_to(base):
        raise ValueError("path escapes project/worktree")
    if protect_secrets and sensitive_path(target.relative_to(base)):
        raise ValueError("sensitive resolved path")
    return target


def classify_command(
    command: str, *, root: Path, cwd: Path | None = None
) -> Classification:
    """Allow only a small plain-command grammar; never infer shell safety."""
    try:
        args = shlex.split(command)
        base = (cwd or root).resolve(strict=True)
        if not base.is_relative_to(root.resolve(strict=True)):
            return Classification.REQUIRE_APPROVAL
    except (ValueError, OSError, RuntimeError):
        return Classification.DENY
    if not args:
        return Classification.DENY
    words = re.findall(r"[A-Za-z_][A-Za-z_0-9.-]*", command)
    if any(word in FORBIDDEN_COMMANDS for word in words):
        return Classification.DENY
    if any(
        arg.split("=", 1)[0] in {"--privileged", "--cap-add", "--device"}
        for arg in args
    ):
        return Classification.DENY
    if "docker.sock" in command or re.search(
        r"rm\s+.*-[A-Za-z]*[rf][A-Za-z]*\s+[/~]", command
    ):
        return Classification.DENY
    if args[0].split("/")[-1] == "rm" and any(
        a.startswith("-") and "r" in a and "f" in a for a in args[1:]
    ):
        return Classification.DENY
    for arg in args:
        # Also inspect paths embedded in flags, assignments, and URLs.
        for value in re.split(r"[=,:]", arg):
            if any(value == p or value.startswith(p + "/") for p in SYSTEM_ROOTS):
                return Classification.DENY
            if sensitive_path(Path(value)):
                return Classification.DENY
    if any(c in command for c in "\n\r;&|<>`$*?{}()\\"):
        return Classification.REQUIRE_APPROVAL
    if any(a.startswith(("~", "../")) or a == ".." for a in args):
        return Classification.REQUIRE_APPROVAL
    for arg in args[1:]:
        if arg.startswith("-"):
            continue
        if "/" in arg or (base / arg).exists():
            try:
                resolve_project_path(root, base / arg)
            except (ValueError, OSError, RuntimeError):
                return Classification.DENY
    # Absolute executable paths and wrappers are never auto-approved.
    if "/" in args[0]:
        return Classification.REQUIRE_APPROVAL
    tool, rest = args[0], args[1:]
    if tool == "git":
        if not rest or rest[0] not in {"status", "diff", "log", "branch"}:
            return Classification.REQUIRE_APPROVAL
        # Reject config overrides, external diff/textconv, output files, and
        # branch mutations. User/repo Git config is still executable; see threat model.
        if any(
            a.startswith(("--exec", "--ext", "--textconv", "--output")) for a in rest
        ):
            return Classification.REQUIRE_APPROVAL
        if rest[0] == "branch" and rest[1:] not in (
            [],
            ["--show-current"],
            ["--list"],
            ["-a"],
            ["-r"],
        ):
            return Classification.REQUIRE_APPROVAL
        return Classification.AUTO_ALLOW
    tests = {
        ("npm", "test"),
        ("npm", "run", "test"),
        ("npm", "run", "lint"),
        ("composer", "test"),
        ("dotnet", "test"),
        ("mvn", "test"),
        ("gradle", "test"),
    }
    if tuple(args) in tests or args == ["pytest"]:
        return Classification.AUTO_ALLOW
    if tool in {"pwd", "ls", "cat", "head", "tail", "wc", "grep", "rg", "find"}:
        if tool == "find" and any(
            a not in {"-name", "-type", "-maxdepth", "-mindepth", "-print"}
            for a in rest
            if a.startswith("-")
        ):
            return Classification.REQUIRE_APPROVAL
        if tool == "rg" and any(a.startswith(("--pre", "--search-zip")) for a in rest):
            return Classification.REQUIRE_APPROVAL
        if any(a.startswith(("--include", "--exclude")) and "=" in a for a in rest):
            return Classification.REQUIRE_APPROVAL
        return Classification.AUTO_ALLOW
    return Classification.REQUIRE_APPROVAL


@dataclass(frozen=True, slots=True)
class ApprovalScope:
    user_id: int
    chat_id: int
    root: Path
    project: str = "unknown"
    branch: str | None = None
    topic_id: int | None = None


@dataclass(slots=True)
class PendingApproval:
    scope: ApprovalScope
    thread_id: str
    turn_id: str
    request_id: str
    command_digest: str
    expires_at: float
    event: anyio.Event = field(default_factory=anyio.Event)
    accepted: bool = False
    result: str = "pending"


class ApprovalStore:
    def __init__(self, *, ttl: float = 120, clock=time.monotonic) -> None:
        if not 0 < ttl <= 3600:
            raise ValueError("approval TTL must be within (0, 3600]")
        self.ttl = ttl
        self.clock = clock
        self.pending: dict[str, PendingApproval] = {}

    def create(
        self,
        scope: ApprovalScope,
        thread_id: str,
        turn_id: str,
        request_id: str,
        command: str,
    ) -> tuple[str, PendingApproval]:
        token = secrets.token_urlsafe(18)
        pending = PendingApproval(
            scope,
            thread_id,
            turn_id,
            request_id,
            hashlib.sha256(command.encode()).hexdigest(),
            self.clock() + self.ttl,
        )
        self.pending[token] = pending
        return token, pending

    def decide(
        self, token: str, *, user_id: int | None, chat_id: int, accept: bool
    ) -> bool:
        pending = self.pending.get(token)
        if (
            pending is None
            or pending.scope.user_id != user_id
            or pending.scope.chat_id != chat_id
        ):
            return False
        self.pending.pop(token)
        if self.clock() >= pending.expires_at:
            pending.result = "expired"
            pending.event.set()
            return False
        pending.accepted = accept
        pending.result = "accepted" if accept else "denied"
        pending.event.set()
        return True

    def invalidate_request(self, thread_id: str, request_id: str) -> None:
        for token, pending in list(self.pending.items()):
            if pending.thread_id == thread_id and pending.request_id == request_id:
                self.pending.pop(token)
                pending.result = "request_closed"
                pending.event.set()

    def invalidate_turn(self, thread_id: str, turn_id: str) -> None:
        for token, pending in list(self.pending.items()):
            if pending.thread_id == thread_id and pending.turn_id == turn_id:
                self.pending.pop(token)
                pending.result = "turn_closed"
                pending.event.set()


class AuditLog:
    """Append before allowing; an unwritable audit sink fails the request closed."""

    def __init__(self, path: Path | None) -> None:
        self.path = path

    def write(self, event: str, **fields: Any) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Arguments/output can contain arbitrary credentials. Store only a
        # sanitized preview; opaque bodies are represented by a digest.
        row = {"timestamp": datetime.now(UTC).isoformat(), "event": event, **fields}
        payload = json.dumps(_redact_value(row, {}), default=str, ensure_ascii=False)
        fd = os.open(
            self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        try:
            os.write(fd, (payload + "\n").encode())
        finally:
            os.close(fd)


def command_preview(command: str) -> str:
    """Sanitize credential-shaped literals before emitting a command preview."""
    result = _redact_text(command)
    result = re.sub(
        r"(?i)(--(?:password|token|api-key|secret)\s+)\S+", r"\1[REDACTED]", result
    )
    # Inline programs and shell expansions may embed arbitrary credentials.
    if any(marker in command for marker in (" -c ", " -e ", "$(", "`", "PRIVATE KEY")):
        return "[opaque command body redacted; SHA256 identifies the exact request]"
    return result[:3000]


CURRENT_GATEWAY: ContextVar[Any] = ContextVar("approval_gateway", default=None)
CURRENT_SCOPE: ContextVar[ApprovalScope | None] = ContextVar(
    "approval_scope", default=None
)

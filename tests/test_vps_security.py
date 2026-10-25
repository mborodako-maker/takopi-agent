from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import anyio
import pytest

from takopi.security import (
    ApprovalScope,
    ApprovalStore,
    AuditLog,
    Classification as C,
    classify_command,
    classify_file_changes,
    command_preview,
    resolve_project_path,
)
from takopi.telegram.approval import TelegramApprovalGateway, classify_patch
from takopi.telegram.files import deny_reason, resolve_path_within_root, zip_directory
from takopi.telegram.types import TelegramCallbackQuery, TelegramIncomingMessage
from takopi.telegram.loop import run_main_loop
from takopi.runners.codex import _AppServerClient, _AppServerTurnControl
from tests.telegram_fakes import FakeBot, FakeTransport, make_cfg


@pytest.mark.parametrize(
    "command",
    [
        "cat hello.txt",
        "git status",
        "git diff",
        "git log",
        "git branch",
        "git branch --show-current",
        "find . -name hello.txt",
        "rg hello .",
        "grep hello hello.txt",
        "npm test",
        "npm run test",
        "npm run lint",
        "pytest",
        "composer test",
        "dotnet test",
        "mvn test",
        "gradle test",
        "pwd",
        "ls",
        "head hello.txt",
        "tail hello.txt",
        "wc hello.txt",
    ],
)
def test_auto_allow(tmp_path, command):
    assert classify_command(command, root=tmp_path) == C.AUTO_ALLOW


@pytest.mark.parametrize(
    "command",
    [
        "git push",
        "git merge other",
        "git reset --hard",
        "git clean -fd",
        "git checkout -- file",
        "git rebase main",
        "npm install",
        "npm uninstall pkg",
        "pip install pkg",
        "pip uninstall pkg",
        "apt install pkg",
        "apt-get update",
        "apk add pkg",
        "yum install pkg",
        "dnf install pkg",
        "systemctl restart foo",
        "service foo restart",
        "docker compose up",
        "git remote set-url origin url",
        "curl https://production.example",
        "psql db",
        "alembic upgrade head",
        "deploy",
        "rm many-files",
        "python task.py",
        "git branch -D main",
        "git diff --output=result",
        "git diff --ext-diff",
        "find . -exec echo x +",
        "rg --pre=tool hello",
        "cat file; git push",
        "cat $(pwd)",
        "cat ../outside",
        "cat ~",
        "env cat file",
        "/bin/cat file",
    ],
)
def test_require_approval(tmp_path, command):
    assert classify_command(command, root=tmp_path) == C.REQUIRE_APPROVAL


@pytest.mark.parametrize(
    "command",
    [
        "sudo apt install pkg",
        "/usr/bin/sudo id",
        "su codex",
        "mount /data",
        "umount /data",
        "cat /etc/passwd",
        "cat /root/file",
        "cat /proc/self/environ",
        "ls /sys",
        "cat /dev/zero",
        "cat /var/run/docker.sock",
        "rm -rf /",
        "rm -rf ./files",
        "chmod 777 /etc/ssh/sshd_config",
        "chown codex /root",
        "cat .ssh/id_ed25519",
        "cat .env",
        "cat nested/auth.json",
        "cat private.key",
        "cat credentials.json",
        "iptables -L",
        "nft list ruleset",
        "reboot",
        "shutdown now",
        "poweroff",
        "useradd foo",
        "passwd foo",
    ],
)
def test_denied(tmp_path, command):
    assert classify_command(command, root=tmp_path) == C.DENY


def test_paths_and_symlinks(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    (root / ".env").write_text("private")
    (root / "alias").symlink_to(root / ".env")
    assert resolve_project_path(root, "hello.txt") == root / "hello.txt"
    for value in [
        "../outside",
        "link/file",
        "alias",
        ".env",
        ".git/config",
        str(outside),
    ]:
        with pytest.raises(ValueError):
            resolve_project_path(root, value)
    assert classify_command("cat alias", root=root) == C.DENY
    assert classify_command("cat link/file", root=root) == C.DENY
    assert resolve_path_within_root(root, Path("alias")) is None
    assert classify_command("pwd", root=root, cwd=outside) == C.REQUIRE_APPROVAL
    assert classify_command("", root=root) == C.DENY
    assert classify_command("'", root=root) == C.DENY


@pytest.mark.parametrize(
    "command",
    [
        "sudo apt install pkg",
        "s'u'do id",
        r"s\udo id",
        "'rm' '-rf' /",
        "cat /etc/passwd",
        "cat /tmp/../etc/passwd",
        "cat /var/run/docker.so''ck",
        "grep -f/etc/passwd hello.txt",
        "rg --file=/proc/self/environ hello.txt",
        "docker run --privileged image",
    ],
)
@pytest.mark.parametrize("external_cwd", [False, True])
def test_unconditional_denials_survive_decoding_and_external_cwd(
    tmp_path, command, external_cwd
):
    root = tmp_path / "project"
    root.mkdir()
    cwd = tmp_path if external_cwd else root
    assert classify_command(command, root=root, cwd=cwd) == C.DENY


@pytest.mark.parametrize(
    "command",
    [
        "grep -R hello .",
        "grep -fpatterns.txt hello.txt",
        "rg --hidden hello .",
        "rg --no-ignore hello .",
        "rg -L hello .",
        "wc --files0-from=paths.txt",
        "find -L .",
    ],
)
def test_reader_options_do_not_inherit_auto_allow(tmp_path, command):
    assert classify_command(command, root=tmp_path) == C.REQUIRE_APPROVAL


def test_option_paths_resolve_symlinks_and_system_cwd_is_denied(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "link").symlink_to(tmp_path, target_is_directory=True)
    assert classify_command("rg --file=link/patterns hello", root=root) == C.DENY
    assert classify_command("pwd", root=root, cwd=Path("/etc")) == C.DENY


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ([{"path": "a.txt", "kind": {"type": "add"}}], C.AUTO_ALLOW),
        ([{"path": "a.txt", "kind": {"type": "delete"}}], C.REQUIRE_APPROVAL),
        (
            [{"path": "a.txt", "kind": {"type": "update", "move_path": "b.txt"}}],
            C.AUTO_ALLOW,
        ),
        (
            [{"path": "a.txt", "kind": {"type": "update", "move_path": "../escape"}}],
            C.DENY,
        ),
        ([{"path": "a.txt", "kind": {"type": "update", "move_path": ".env"}}], C.DENY),
        ([{"path": "a.txt", "kind": {"type": "mystery"}}], C.DENY),
        ([{"path": "a.txt", "kind": {"type": []}}], C.DENY),
        ([{"path": "a.txt"}], C.DENY),
        ([None], C.DENY),
        ([], C.DENY),
        ("invalid", C.DENY),
        ([{"path": "a.txt", "kind": {"type": "add"}}] * 21, C.REQUIRE_APPROVAL),
    ],
)
def test_file_changes_validate_kind_and_both_move_paths(tmp_path, changes, expected):
    assert classify_file_changes(changes, root=tmp_path) == expected


def test_file_change_move_symlink_escape(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "link").symlink_to(tmp_path, target_is_directory=True)
    changes = [{"path": "a.txt", "kind": {"type": "update", "move_path": "link/a"}}]
    assert classify_file_changes(changes, root=root) == C.DENY


@pytest.mark.parametrize(
    "name",
    [
        ".env",
        ".env.production",
        "nested/.env.local",
        "private.pem",
        "nested/private.key",
        ".ssh/id_rsa",
        "nested/.ssh/id_ed25519",
        "auth.json",
        "nested/auth.json",
        "credentials.yaml",
        "MY_SECRET.txt",
        ".git/config",
        ".codex/config.toml",
        ".takopi/state.json",
    ],
)
def test_mandatory_transfer_denials(name):
    assert deny_reason(Path(name), []) is not None


def test_zip_does_not_include_secrets_or_symlinks(tmp_path):
    import io
    import zipfile

    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "hello.txt").write_text("hello")
    (folder / ".env.production").write_text("private")
    (folder / "alias").symlink_to(folder / ".env.production")
    with zipfile.ZipFile(
        io.BytesIO(zip_directory(tmp_path, Path("folder"), []))
    ) as archive:
        assert archive.namelist() == ["folder/hello.txt"]


def test_store_user_chat_ttl_replay_and_turn_binding(tmp_path):
    now = [0.0]
    store = ApprovalStore(ttl=10, clock=lambda: now[0])
    scope = ApprovalScope(42, 123, tmp_path)
    token, p = store.create(scope, "thread", "turn", "request", "git push")
    assert p.request_id == "request" and p.turn_id == "turn"
    assert not store.decide(token, user_id=99, chat_id=123, accept=True)
    assert not store.decide(token, user_id=42, chat_id=456, accept=True)
    assert store.decide(token, user_id=42, chat_id=123, accept=True)
    assert p.accepted
    assert not store.decide(token, user_id=42, chat_id=123, accept=True)
    token, p = store.create(scope, "thread", "turn", "next", "git push")
    now[0] = 10
    assert not store.decide(token, user_id=42, chat_id=123, accept=True)
    assert p.result == "expired" and not p.accepted
    token, p = store.create(scope, "thread", "turn", "other", "git push")
    store.invalidate_turn("wrong", "turn")
    assert token in store.pending
    store.invalidate_turn("thread", "turn")
    assert token not in store.pending and p.result == "turn_closed"
    with pytest.raises(ValueError):
        ApprovalStore(ttl=0)


def test_audit_redaction_and_no_auth_content(tmp_path):
    path = tmp_path / "audit.jsonl"
    AuditLog(path).write(
        "test", requested_command=command_preview("curl --token secret-value")
    )
    AuditLog(path).write(
        "test", requested_command=command_preview("echo token=private-value")
    )
    text = path.read_text()
    assert "secret-value" not in text and "private-value" not in text
    assert all("timestamp" in json.loads(line) for line in text.splitlines())
    assert "opaque" in command_preview("python -c 'hidden program'")
    assert command_preview("git push origin main") == "git push origin main"


def test_patch_paths(tmp_path):
    assert (
        classify_patch(
            "*** Begin Patch\n*** Add File: hello.txt\n+hello\n*** End Patch", tmp_path
        )
        == C.AUTO_ALLOW
    )
    assert classify_patch("*** Delete File: hello.txt", tmp_path) == C.REQUIRE_APPROVAL
    for patch in ["unknown", "*** Update File: ../outside", "*** Add File: .env"]:
        assert classify_patch(patch, tmp_path) == C.DENY


@pytest.mark.anyio
async def test_telegram_approval_ui_and_single_use(tmp_path):
    bot = FakeBot()
    gateway = TelegramApprovalGateway(bot, audit_path=tmp_path / "audit.jsonl", ttl=1)
    scope = ApprovalScope(42, 123, tmp_path, "project", "feature", 77)
    result = []

    async def request():
        result.append(
            await gateway.request(
                scope=scope,
                thread_id="thread",
                turn_id="turn",
                request_id="request",
                command="git push origin main",
                classification=C.REQUIRE_APPROVAL,
            )
        )

    async with anyio.create_task_group() as tg:
        tg.start_soon(request)
        while not bot.send_calls:
            await anyio.sleep(0)
        prompt = bot.send_calls[0]
        assert prompt["message_thread_id"] == 77
        assert "git push origin main" in prompt["text"]
        data = prompt["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        query = TelegramCallbackQuery("telegram", 123, 1, "callback", data, 99)
        await gateway.callback(query)
        assert gateway.store.pending
        await gateway.callback(replace(query, sender_id=42))
    assert result == [True]
    await gateway.callback(replace(query, sender_id=42))
    assert "недействителен" in bot.callback_calls[-1]["text"]
    assert not gateway.store.pending
    assert "accepted" in (tmp_path / "audit.jsonl").read_text()


@pytest.mark.anyio
async def test_timeout_delivery_deny_auto_and_cancel(tmp_path, monkeypatch):
    bot = FakeBot()
    gateway = TelegramApprovalGateway(
        bot, audit_path=tmp_path / "audit.jsonl", ttl=0.01
    )
    scope = ApprovalScope(42, 123, tmp_path)
    args: dict[str, Any] = dict(  # noqa: C408
        scope=scope,
        thread_id="thread",
        turn_id="turn",
        request_id="r",
        command="git push",
    )
    assert not await gateway.request(**args, classification=C.REQUIRE_APPROVAL)
    assert not gateway.store.pending
    assert not await gateway.request(**args, classification=C.DENY)
    assert "⛔" in bot.send_calls[-1]["text"]
    assert await gateway.request(**args, classification=C.AUTO_ALLOW)

    async def missing(**kwargs):
        return None

    monkeypatch.setattr(bot, "send_message", missing)
    assert not await gateway.request(**args, classification=C.REQUIRE_APPROVAL)
    gateway.register_turn("thread", "turn", scope)
    token, p = gateway.store.create(scope, "thread", "turn", "r", "git push")
    gateway.close_turn("thread", "turn")
    assert token not in gateway.store.pending and p.result == "turn_closed"


@pytest.mark.anyio
async def test_hook_unix_socket_and_context_persistence(tmp_path):
    gateway = TelegramApprovalGateway(
        FakeBot(), audit_path=tmp_path / "audit", state_path=tmp_path / "contexts"
    )
    gateway.remember(123, 1, 42, None)
    from takopi.context import RunContext
    from takopi.security import CURRENT_SCOPE

    with gateway.bind_run(
        123, 1, tmp_path, RunContext(project="proj", branch="fix/test")
    ):
        scope = CURRENT_SCOPE.get()
        assert scope is not None
        gateway.register_turn("thread", "turn", scope)
    assert gateway.last_session(123, None, 42) == "thread"
    assert gateway.last_context(123, None, 99) is None
    restored = TelegramApprovalGateway(
        FakeBot(), audit_path=None, state_path=tmp_path / "contexts"
    )
    restored_context = restored.last_context(123, None, 42)
    assert restored_context is not None
    assert restored_context.branch == "fix/test"
    socket_path = tmp_path / "approval.sock"
    await gateway.start_hook_server(socket_path)

    async def call(data):
        stream = await anyio.connect_unix(socket_path)
        async with stream:
            await stream.send(json.dumps(data).encode() + b"\n")
            return json.loads(await stream.receive())

    data = dict(  # noqa: C408
        session_id="thread",
        turn_id="turn",
        hook_event_name="PreToolUse",
        tool_name="Bash",
        tool_input={"command": "git status"},
    )
    assert (await call(data))["allow"]
    assert not (await call({**data, "tool_input": {"command": "sudo id"}}))["allow"]
    assert not (await call({**data, "turn_id": "other"}))["allow"]
    assert not (await call({**data, "tool_name": "mcp_unknown"}))["allow"]
    assert (
        await call(
            {
                **data,
                "tool_name": "apply_patch",
                "tool_input": {"command": "*** Add File: hello.txt"},
            }
        )
    )["allow"]
    assert (await call({**data, "tool_name": "update_plan"}))["allow"]
    assert (await call({**data, "hook_event_name": "PostToolUse"}))["allow"]
    await gateway.stop()
    assert not gateway.turns


@pytest.mark.anyio
async def test_app_server_gateway_accept_and_interrupt_revoke(tmp_path, monkeypatch):
    gateway = TelegramApprovalGateway(FakeBot(), audit_path=None)
    scope = ApprovalScope(42, 123, tmp_path)
    gateway.register_turn("thread", "turn", scope)
    client = _AppServerClient(codex_cmd="codex", extra_args=[])
    client.gateway = gateway
    writes = []

    async def write(payload):
        writes.append(payload)

    monkeypatch.setattr(client, "_write", write)
    message = {
        "id": "r",
        "method": "item/commandExecution/requestApproval",
        "params": {
            "threadId": "thread",
            "turnId": "turn",
            "itemId": "i",
            "command": "git push",
        },
    }
    async with anyio.create_task_group() as tg:
        tg.start_soon(client._respond_server_request, message)
        while not gateway.store.pending:
            await anyio.sleep(0)
        token = next(iter(gateway.store.pending))
        gateway.store.decide(token, user_id=42, chat_id=123, accept=True)
    assert writes[-1]["result"] == {"decision": "accept"}
    # Actual turn/interrupt uses the upstream control API and revokes pending capabilities.
    token, pending = gateway.store.create(scope, "thread", "turn", "r2", "git push")
    requests = []

    async def rpc(method, params):
        requests.append((method, params))
        return {}

    monkeypatch.setattr(client, "request", rpc)
    control = _AppServerTurnControl(client, "thread", "turn")
    assert await control.interrupt()
    assert requests[-1] == ("turn/interrupt", {"threadId": "thread", "turnId": "turn"})
    assert token not in gateway.store.pending and pending.result == "turn_closed"
    await client._respond_server_request(message)
    assert writes[-1]["result"] == {"decision": "decline"}


@pytest.mark.anyio
async def test_empty_allowlist_and_missing_sender_are_rejected():
    for users, sender in [((), 123), ((123,), None)]:
        transport = FakeTransport()
        cfg = replace(make_cfg(transport), allowed_user_ids=users)

        async def poller(_cfg, sender=sender):
            yield TelegramIncomingMessage(
                "telegram", 123, 1, "/projects", None, None, sender
            )
            yield TelegramCallbackQuery(
                "telegram", 123, 1, "q", "approval:x:yes", sender
            )

        await run_main_loop(cfg, poller)
        assert not transport.send_calls
        assert isinstance(cfg.bot, FakeBot)
        assert not cfg.bot.callback_calls


def test_workspace_root_and_privileged_container_denied(tmp_path, monkeypatch):
    from takopi.worktrees import _validate_workspace, WorktreeError

    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setenv("TAKOPI_WORKSPACE_ROOT", str(root))
    _validate_workspace(root / "project/.worktrees/fix")
    (root / "escape").symlink_to(tmp_path)
    with pytest.raises(WorktreeError):
        _validate_workspace(root / "escape/outside")
    assert classify_command("docker run --privileged image", root=root) == C.DENY
    assert classify_command("docker run --privileged=true image", root=root) == C.DENY


def test_request_resolution_revokes_approval(tmp_path):
    store = ApprovalStore()
    scope = ApprovalScope(42, 123, tmp_path)
    token, pending = store.create(scope, "thread", "turn", "request", "git push")
    store.invalidate_request("thread", "other")
    assert token in store.pending
    store.invalidate_request("thread", "request")
    assert token not in store.pending and pending.result == "request_closed"


@pytest.mark.anyio
async def test_file_change_approval_and_protected_path(tmp_path, monkeypatch):
    gateway = TelegramApprovalGateway(FakeBot(), audit_path=None)
    gateway.register_turn("thread", "turn", ApprovalScope(42, 123, tmp_path))
    client = _AppServerClient(codex_cmd="codex", extra_args=[])
    client.gateway = gateway
    writes = []

    async def write(payload):
        writes.append(payload)

    monkeypatch.setattr(client, "_write", write)
    message = {
        "id": "r",
        "method": "item/fileChange/requestApproval",
        "params": {"threadId": "thread", "turnId": "turn", "itemId": "item"},
    }
    client._items[("turn", "item")] = {
        "changes": [{"path": "hello.txt", "kind": {"type": "add"}}]
    }
    await client._respond_server_request(message)
    assert writes[-1]["result"]["decision"] == "accept"
    client._items[("turn", "item")] = {
        "changes": [{"path": ".env", "kind": {"type": "add"}}]
    }
    await client._respond_server_request(message)
    assert writes[-1]["result"]["decision"] == "decline"
    await client._respond_server_request(
        {
            "id": "p",
            "method": "item/permissions/requestApproval",
            "params": {"permissions": {"network": True}},
        }
    )
    assert writes[-1]["result"]["permissions"] == {}


@pytest.mark.anyio
async def test_approval_ttl_includes_delivery_time(tmp_path, monkeypatch):
    gateway = TelegramApprovalGateway(FakeBot(), audit_path=None, ttl=0.01)

    async def stalled(**kwargs):
        await anyio.sleep(60)

    monkeypatch.setattr(gateway.bot, "send_message", stalled)
    with anyio.fail_after(1):
        accepted = await gateway.request(
            scope=ApprovalScope(42, 123, tmp_path),
            thread_id="thread",
            turn_id="turn",
            request_id="r",
            command="git push",
            classification=C.REQUIRE_APPROVAL,
        )
    assert not accepted and not gateway.store.pending


def test_external_cwd_cannot_hide_protected_symlink(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "alias").symlink_to("/etc/passwd")
    assert classify_command("cat alias", root=root, cwd=outside) == C.DENY

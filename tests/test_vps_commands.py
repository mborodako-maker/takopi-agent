from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import anyio
import pytest

from takopi.config import ProjectConfig, ProjectsConfig
from takopi.context import RunContext
from takopi.runner_bridge import RunningTask
from takopi.security import ApprovalScope
from takopi.telegram.approval import TelegramApprovalGateway
from takopi.telegram.commands import vps
from takopi.telegram.types import TelegramIncomingMessage
from takopi.transport import MessageRef
from tests.telegram_fakes import FakeBot, FakeTransport, make_cfg


async def setup_project(tmp_path: Path, *, test_command="pytest"):
    root = tmp_path / "project"
    root.mkdir()
    await anyio.run_process(["git", "init", "-b", "main", str(root)])
    await anyio.run_process(
        ["git", "config", "user.email", "test@example.invalid"], cwd=root
    )
    await anyio.run_process(["git", "config", "user.name", "Test"], cwd=root)
    (root / "tracked.txt").write_text("old\n")
    await anyio.run_process(["git", "add", "tracked.txt"], cwd=root)
    await anyio.run_process(["git", "commit", "-m", "initial"], cwd=root)
    transport = FakeTransport()
    cfg = make_cfg(transport)
    runtime = cfg.runtime
    # Use the existing projects config; no custom worktree resolver.
    runtime._projects = ProjectsConfig(
        {
            "proj": ProjectConfig(
                "proj",
                root,
                Path(".worktrees"),
                worktree_base="main",
                test_command=test_command,
            )
        },
        default_project="proj",
    )
    gateway = TelegramApprovalGateway(FakeBot(), audit_path=tmp_path / "audit", ttl=1)
    cfg = replace(cfg, approval_gateway=gateway, bot=gateway.bot)
    msg = TelegramIncomingMessage("telegram", 123, 1, "", None, None, 123)
    return root, cfg, transport, msg


@pytest.mark.anyio
async def test_projects_status_abort_and_saved_context(tmp_path):
    root, cfg, transport, msg = await setup_project(tmp_path)
    context = RunContext(project="proj")
    gateway = cfg.approval_gateway
    gateway.register_turn("session", "turn", ApprovalScope(123, 123, root, "proj"))
    active = RunningTask(context=context)
    tasks = {MessageRef(channel_id=123, message_id=99): active}
    for cmd in ["projects", "status", "abort"]:
        await vps.handle_vps_command(cfg, msg, cmd, "", None, tasks)
    texts = [c["message"].text for c in transport.send_calls]
    assert "/proj" in texts[0]
    assert "session" in texts[1] and "Elapsed:" in texts[1]
    assert active.cancel_requested.is_set()
    await vps.handle_vps_command(cfg, msg, "abort", "", context, {})
    assert "No active" in transport.send_calls[-1]["message"].text


@pytest.mark.anyio
async def test_diff_tracks_changes_untracked_and_blocks_secrets(tmp_path):
    root, cfg, transport, msg = await setup_project(tmp_path)
    (root / "tracked.txt").write_text("new\n")
    (root / "hello.txt").write_text("hello\n")
    (root / ".env").write_text("NEVER_EXPORT_SECRET\n")
    (root / "private.key").write_text("NEVER_EXPORT_KEY\n")
    await anyio.run_process(["git", "add", ".env", "private.key"], cwd=root)
    await vps.handle_vps_command(cfg, msg, "diff", "", None, {})
    text = "\n".join(c["message"].text for c in transport.send_calls)
    assert "+new" in text and "+hello" in text and "changed" in text
    assert "NEVER_EXPORT" not in text
    (root / "tracked.txt").write_text("line\n" * 1000)
    await vps.handle_vps_command(cfg, msg, "diff", "", None, {})
    assert cfg.bot.document_calls[-1]["filename"] == "changes.diff"


@pytest.mark.anyio
async def test_tests_command_is_configured_argv_under_sandbox(tmp_path, monkeypatch):
    root, cfg, transport, msg = await setup_project(tmp_path)
    calls = []

    async def run(argv, cwd, **kwargs):
        calls.append(argv)
        return 0, "tests passed"

    monkeypatch.setattr(vps, "run_bounded", run)
    await vps.handle_vps_command(cfg, msg, "tests", "", None, {})
    assert calls[-1][0:2] == ["codex", "sandbox"]
    assert calls[-1][-2:] == ["--", "pytest"]
    await vps.handle_vps_command(cfg, msg, "tests", "rm -rf /", None, {})
    assert "accepts project directives only" in transport.send_calls[-1]["message"].text
    cfg.runtime._projects.projects["proj"] = replace(
        cfg.runtime._projects.projects["proj"], test_command="sudo id"
    )
    await vps.handle_vps_command(cfg, msg, "tests", "", None, {})
    assert "outside the safe" in transport.send_calls[-1]["message"].text


@pytest.mark.anyio
async def test_commit_requires_confirmation_and_checks_index(tmp_path, monkeypatch):
    root, cfg, transport, msg = await setup_project(tmp_path)
    await vps.handle_vps_command(cfg, msg, "commit", "", None, {})
    assert "No staged" in transport.send_calls[-1]["message"].text
    (root / "tracked.txt").write_text("new\n")
    await anyio.run_process(["git", "add", "tracked.txt"], cwd=root)
    requests = []

    async def approve(**kwargs):
        requests.append(kwargs)
        return True

    monkeypatch.setattr(cfg.approval_gateway, "request", approve)
    await vps.handle_vps_command(cfg, msg, "commit", "", None, {})
    assert requests[0]["scope"].user_id == 123
    assert "Commit exit code: 0" in transport.send_calls[-1]["message"].text
    assert "push" not in requests[0]["command"]
    (root / "tracked.txt").write_text("next\n")
    await anyio.run_process(["git", "add", "tracked.txt"], cwd=root)

    async def mutate(**kwargs):
        (root / "tracked.txt").write_text("raced\n")
        await anyio.run_process(["git", "add", "tracked.txt"], cwd=root)
        return True

    monkeypatch.setattr(cfg.approval_gateway, "request", mutate)
    await vps.handle_vps_command(cfg, msg, "commit", "", None, {})
    assert "Index changed" in transport.send_calls[-1]["message"].text


@pytest.mark.anyio
async def test_worktree_resolution_and_symlink_escape(tmp_path):
    root, cfg, transport, msg = await setup_project(tmp_path)
    context = RunContext(project="proj", branch="test/telegram-agent")
    path = cfg.runtime.resolve_run_cwd(context)
    assert path == root / ".worktrees/test/telegram-agent"
    assert (path / "tracked.txt").is_file()
    assert cfg.runtime.resolve_run_cwd(context) == path
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / ".worktrees/escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(Exception, match="escapes"):
        cfg.runtime.resolve_run_cwd(RunContext(project="proj", branch="escape/branch"))


@pytest.mark.anyio
async def test_bounded_process_and_hook_errors(tmp_path):
    rc, output = await vps.run_bounded(["/bin/echo", "hello"], tmp_path)
    assert rc == 0 and "hello" in output
    rc, _ = await vps.run_bounded(["/bin/echo", "hello"], tmp_path, max_bytes=1)
    assert rc == 1
    rc, _ = await vps.run_bounded(["/bin/sleep", "5"], tmp_path, timeout=0.01)
    assert rc == 124
    result = await anyio.run_process(
        [sys.executable, "-m", "takopi.codex_hook"],
        input=b"bad json",
    )
    assert (
        json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    )

"""These tests require the normal Takopi development dependencies."""

import pytest

from takopi.codex_env import safe_codex_env
from takopi.runners.codex import CodexRunner, _AppServerClient


def test_exec_runner_uses_safe_environment(monkeypatch):
    monkeypatch.setenv("BYBIT_API_SECRET", "fixture-secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fixture-token")
    runner = CodexRunner(codex_cmd="codex", extra_args=[])
    assert runner.env(state=None) == safe_codex_env()
    assert "BYBIT_API_SECRET" not in runner.env(state=None)


@pytest.mark.anyio
async def test_app_server_spawn_uses_safe_environment(monkeypatch):
    monkeypatch.setenv("BYBIT_API_KEY", "fixture-key")
    monkeypatch.setenv("DATABASE_PASSWORD", "fixture-password")
    seen = {}

    async def fail_spawn(cmd, **kwargs):
        seen.update(kwargs)
        raise OSError("fixture stops before starting any real process")

    monkeypatch.setattr("takopi.runners.codex.anyio.open_process", fail_spawn)
    with pytest.raises(OSError):
        await _AppServerClient(codex_cmd="codex", extra_args=[]).start()
    assert seen["env"] == safe_codex_env()
    assert "BYBIT_API_KEY" not in seen["env"]
    assert "DATABASE_PASSWORD" not in seen["env"]

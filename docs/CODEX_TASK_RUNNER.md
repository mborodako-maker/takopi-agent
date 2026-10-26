# Predefined Codex maintenance tasks

Commands: `/codex_task list`, `/codex_task run NAME.md`, `/codex_task status`,
`/trader_upgrade`. Select a configured project containing `codex_tasks/`.
The existing numeric admin whitelist applies to every command.

Only bounded UTF-8 regular `.md` files with simple names are accepted. Absolute
paths, traversal, shell syntax, symlinks and non-regular files are rejected.
No filename or Telegram text becomes a shell command. A persistent flock outside
project roots prevents concurrent tasks across worktrees/processes.

Requires `codex.mode = "app_server"`, persistent config and the existing approval
gateway. A dedicated worker uses JSON-RPC input through stdin, managed hooks,
admin/worktree-bound approvals, untrusted policy and no-network workspace sandbox.
Timeout/shutdown stops that worker before releasing its lock; conversational
workers are unaffected. Telegram cancellation is not added.

State/logs: `<parent of takopi.toml>/logs/codex_tasks/`. Metadata contains IDs,
filename/hash, user ID, times, status and log paths. `exit_code` is normalized
turn result 0/1, not app-server OS exit status; timeout is 124 after 3600 seconds.
Only bounded redacted final output is retained. The bot acknowledges immediately
and sends a concise final report. Model claims about tests need independent checks.

Both Codex runners now inherit only HOME/PATH/USER/LOGNAME/LANG/LC_ALL/CODEX_HOME
and TAKOPI_APPROVAL_SOCKET (managed hook IPC). Stored CLI login remains supported;
API-key env authentication is intentionally unsupported. Sandbox/healthcheck and
direct Codex container maintenance entrypoint are sanitized too. Env filtering
does not isolate readable files or same-UID credentials; preserve SECURITY.md.

Manual execution in an environment without production secrets:

```bash
codex exec - < codex_tasks/CODEX_CRYPTO_TRADER_SKILLS_TASK.md
```

This reruns the task, not a status check. The current task file is supplied by
the separate trader project; put it in the configured worktree's task directory.

`/market BTC`, `/signals`, `/strategy`, `/performance`, `/shadow_status` only read
stored research results from a separately installed crypto-trader-shadow service.
Set trusted bot environment TAKOPI_TRADER_DB; default is
`<parent of takopi.toml>/crypto-trader/shadow.sqlite3`. No Telegram input selects
a DB path. Provision a read-only mount and explicit access to SQLite main/WAL/SHM
files if service users differ. Do not mount bot credentials into the research
worker. Missing state returns unavailable; timestamps do not prove process health.

After applying source changes:

```bash
uv sync --frozen
just check
uv run pytest tests/test_codex_task_service.py tests/test_trader_read_service.py tests/test_codex_env_integration.py --no-cov
```

Only publish/redeploy after the complete checks pass. Source edits alone do not
update a running VPS image. The bot has no Bybit credentials or real executor.

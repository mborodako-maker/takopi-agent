# Takopi and crypto research

Telegram updates pass the numeric user whitelist before routing. Normal prompts
use configured runners; Codex app-server uses the existing approval gateway,
managed hooks and no-network workspace sandbox.

Predefined task files run in a separate app-server process under the same trusted
admin/project/worktree scope. A persistent flock prevents overlapping tasks;
timeout/shutdown closes the worker before releasing the lock. Both Codex process
modes now share an explicit environment allowlist.

Optional crypto read commands open a separately managed SQLite database read-only
with fixed parameterized queries. They never execute project scripts or orders.
Trading models and virtual SHADOW/PAPER results belong to the separate trader
project; the bridge has no Bybit credentials or real Trade Executor.

See docs/CODEX_TASK_RUNNER.md, docs/TRADER_SKILL_ADAPTATION.md and SECURITY.md.

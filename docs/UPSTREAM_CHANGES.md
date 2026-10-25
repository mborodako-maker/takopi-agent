# Upstream merge inventory

Baseline: banteg/takopi v0.23.4 (e0123d4). No wholesale refactoring.

| Core file(s) | Why / plugin feasibility / likely merge conflict |
| --- | --- |
| ids.py, telegram/commands/menu.py | Reserve and advertise VPS commands; additive command-name conflicts. Hyphenated project aliases work as text directives but Telegram bot menu permits only underscores, as upstream. |
| worktrees.py | Validate configured workspace boundary before resolving/creating worktrees; retain standard Git worktree implementation. |
| telegram/backend.py, bridge.py | Startup allowlist requirement and gateway wiring; transport bootstrap cannot be intercepted by command plugin. Backend config fields may conflict. |
| telegram/loop.py | Fail-closed sender dispatch, gateway lifecycle, callback routing and small VPS command dispatch; conflicts around upstream routing/reload. Security checks must remain before all dispatch. |
| telegram/parsing.py | Poll heartbeat and count-only update logging. Keep heartbeat after a successful polling response, including empty responses. |
| runners/codex.py | Replace permissive approvals, run requests asynchronously, scope to turns, cache proposed file changes, revoke on interrupt/disconnect/completion, strip child secrets, explicit sandbox on every turn. Command plugins cannot intercept private JSON-RPC requests; highest conflict risk when upstream changes app-server protocol. |
| telegram/commands/executor.py | Bind authenticated sender and resolved worktree through contextvars for each run (including queued runs). Remove when upstream exposes equivalent per-run approval context. |
| settings.py, config.py | Optional project test_command, configurable file cap, suppress credential inputs in validation errors. Small additive schema conflict risk. |
| transport_runtime.py | Add read-only project_config accessor for commands; upstream could expose a public project context API. |
| runner_bridge.py | Add monotonic started_at for status; additive dataclass field. |
| telegram/files.py, commands/file_transfer.py | Mandatory sensitive-path checks and file cap independent of configurable deny_globs; transport-level security cannot be optional plugin. |
| logging.py | Broader credential/key redaction; conflicts with upstream logging processors. |
| utils/git.py | Disable repository hooks/fsmonitor for bridge Git operations including worktree creation. Preserve flags on upstream merge. |

New isolated modules: security.py (policy/store/audit), codex_hook.py (managed hook),
telegram/approval.py (UI/socket), telegram/commands/vps.py (workflow commands).
VPS commands need selected chat/topic/user context and RunningTasks, which the
current command plugin API does not expose. Kept in a separate module with one
routing hook instead of using private executor internals in an entry-point plugin.
When upstream adds a public context/control API, move these to a command plugin.
Jira remains a future plugin through the existing public command API.

Tests update only affected expectations/fixtures; existing permissive approval
expectations become deny expectations. No tests disabled and no coverage threshold
lowered. Preserve user-owned task files; do not add them to implementation commits.

On upgrades: compare pinned official Codex generated JSON schema; check managed
hooks are actually loaded; test unknown requests, multiple sessions, queued sender
ownership, TTL, interrupt while awaiting approval, restart/resume, file filtering
and worktree symlinks. Rerun `just check` and the VPS manual milestone.

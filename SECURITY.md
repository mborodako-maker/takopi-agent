# Security model

Codex processes now use an explicit environment allowlist for exec/app-server
and maintenance checks. Predefined tasks preserve the app-server gateway and
sandbox; filenames never become shell commands. Crypto report commands only
read SQLite state. See docs/CODEX_TASK_RUNNER.md for state/isolation requirements.

This fork is for one VPS operator and explicitly trusted Telegram users/projects.
It is **not** a multitenant sandbox for hostile repositories. Container isolation,
Codex sandbox, managed hooks and approval requests provide different layers.
Never mount host secrets, Docker socket, host home, /etc, /root or / into it.

## Threats and controls

| Threat | Mitigation / residual risk |
| --- | --- |
| Stolen Telegram bot token | Numeric sender allowlist checked before every message/callback; rotate token via BotFather, revoke credentials, stop bot. Token still grants Telegram bot API access outside this process. |
| Foreign Telegram user | Fail startup on empty/nonpositive IDs; usernames ignored; unauthorized sender IDs logged. Group membership/admin status does not bypass global allowlist. |
| Prompt injection in project files | Treat files/tool output as untrusted. Managed PreToolUse hooks classify local tools; app-server approvals have independent checks. Instructions alone are not a security control. |
| Malicious repository | Never provision production credentials. Disable Git hooks/fsmonitor in bridge Git operations. Review dependencies/test scripts. Test commands execute arbitrary repository code, even when their invocation is AUTO_ALLOW. |
| Dangerous Codex command | Central classifier: AUTO_ALLOW / REQUIRE_APPROVAL / DENY. Unknown shell syntax requires approval; system paths, sudo, recursive force deletion, keys, auth, Docker socket are blocked. No automatic permission grants or session-wide policy amendments. |
| Secret exfiltration | Separate workspace/config/auth; strip Telegram/secret env from Codex, redact structured logs and credential-shaped values; mandatory file-transfer deny rules. Do not put secrets in command arguments, source, outputs or commit diffs. Arbitrarily disguised secrets cannot be recognized reliably by regex. |
| Symlink traversal | Resolve absolute paths against actual project/worktree root; validate both requested and resolved names; skip symlinks in archives. A hostile concurrent same-UID process can race checks; see boundary below. |
| Dependency installation | Requires explicit request approval; read-only image and no root/capabilities prevent OS package mutation. Build dependencies into a reviewed image. Test scripts themselves can install dependencies; restrict worker network. |
| Git push / remote edits | Require single-use owner-bound approval, TTL 120s; no automatic push; never mount production Git credentials. A git remote should be reviewed independently. |
| Production API / database / migrations | No such credentials or integrations provisioned; unknown/network/deploy commands require approval. Configure host egress policy to block private/production destinations. |
| Docker escape | UID/GID 1001, cap_drop ALL, no-new-privileges, read-only rootfs, bounded mounts/tmpfs/PIDs/memory/CPU, no host sockets; patch host kernel/Docker. No container can guarantee protection against kernel vulnerabilities. |
| Stolen VPS / backup | Full disk encryption where appropriate, encrypted offline backups, strong host SSH access, revoke bot/Codex credentials. Backup contains auth/session secrets. |

## Approval guarantees

Callbacks contain a random capability; server memory binds it to user ID, chat,
thread, turn, request ID and SHA256 of the original command. The model cannot
alter a pending command through the Telegram callback. Only the requesting user
in the requesting chat can answer. Wrong-user attempts do not consume a request.
One accepted/denied decision consumes the capability. Timeout, turn completion,
interrupt, app-server disconnect and restart invalidate pending capabilities.
No `acceptForSession`, rule amendment, permission expansion or full-access fallback.
Opaque inline programs are redacted in previews: deny them if the concrete action
cannot be reviewed. A confirmation is not a guarantee that arbitrary code is safe.

The JSON-RPC reader never waits for a human. Concurrent sessions, turn/interrupt
and turn/steer continue while requests wait. Audit failure prevents acceptance.
Audit stores classification, request identity, sanitized command/digest and outcome;
raw tool output and credentials are excluded. PostToolUse records completion, not
necessarily a zero exit status; `/tests` and `/commit` record numeric exit codes.
Rotation: README_VPS.md. Log access is operator-only; logs are not tamper-proof
against compromise of the application UID.

## Enforcement and compatibility limits

The hardened container loads `/etc/codex/requirements.toml` with managed-only
hooks; it must be root-owned/read-only. Gateway runs at a private Unix socket,
no TCP port. Generic Takopi command plugins cannot intercept engine approvals,
so small core changes are required. `codex.mode=exec`, other engines, disabling
hooks and custom plugins are outside the hardened profile. Use the provided
app_server config. On each resumed/new turn, explicit workspace-write sandbox,
network disabled and untrusted approval policy override persisted turn settings.

[Official Codex hooks documentation](https://developers.openai.com/codex/hooks)
identifies special tool paths outside hook coverage and fail-open behavior for
hook errors. Our hook returns an explicit DENY on transport/parsing errors, but
cannot change Codex's behavior if Codex skips or kills the hook itself. Interactive
stdin to an already approved process is not a new PreToolUse event. A shell
allowlist also cannot classify the effects of scripts, binaries or test suites.

Takopi and Codex currently share UID 1001 and auth storage. Codex sandbox protects
writes, but its legacy workspace-write protocol allows broad filesystem reads;
it is not a filesystem confidentiality boundary. After a human approves an
escalation, Codex may execute outside its inner sandbox (still inside Docker).
Same-UID arbitrary code can read auth/config/logs, race file checks, access the
approval socket and modify writable state. Therefore **do not use this image for
hostile code, untrusted tenants, production access, or a promise of absolute
secret isolation**. A separate worker UID/container with restricted filesystem,
network proxy and credential broker is required before expanding that threat model.

Docker's default seccomp/AppArmor may prevent Codex Bubblewrap from creating user
namespaces. Never solve that with privileged, SYS_ADMIN, seccomp=unconfined or
sandbox=disabled. Validate the official sandbox on the target VPS before login
and the milestone; if it fails, the environment is not ready. This environment's
container smoke test encountered that restriction. End-to-end Telegram/login and
sandbox behavior must be checked on the actual VPS; automated tests cannot prove
that milestone without accounts and a compatible kernel/security profile.

## Additional policy validation

Unconditional command denials are evaluated before approval for a cwd outside
the project. The classifier checks shell-decoded words, attached absolute option
paths and symlink-resolved option values. Reader options outside an explicit
list require review. This is conservative parsing, not a general shell proof.
App-server file changes validate both source and move destination, reject unknown
change kinds, and require approval for deletion or more than 20 changes.
Backup rejects canonical destinations under the checkout or selected production
runtime root, including symlink and parent-directory aliases, so archives containing
credentials cannot enter workspace. Production mounts only workspace/config/state/
auth/log directories; the bridge source checkout and host registry credentials are
not mounted. Images are built and published after checks in GitHub Actions.


## Opt-in Bubblewrap compatibility

The dedicated profiles in deploy/security are based on pinned Moby defaults.
They add filtered namespace creation and the mount/chroot operations required by
Bubblewrap, with a separate enforcing AppArmor policy. This deliberately extends
the outer syscall allowlist; it does not disable seccomp/AppArmor or add app
capabilities. Kernel capability checks still gate mounts before entry into an owned
user namespace. More unprivileged namespace kernel code becomes reachable: keep
the host patched and retain the trusted-project threat model above.

The compatibility installer touches only /etc/apparmor.d/takopi-codex and loads
that named profile; it does not change global sysctls or docker-default. An explicit
Docker-administrator installation path uses MAC_ADMIN only in a transient trusted
loader, never in the bot. Profile inputs/loader access are administrator-trusted.
On the target VPS, real enforcement checks passed: project writes work, writes
to sibling/auth paths and IP socket creation fail inside the Codex sandbox.
This supplements the unit tests; it does not establish same-UID confidentiality.

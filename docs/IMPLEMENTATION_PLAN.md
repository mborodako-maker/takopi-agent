# Hardened VPS implementation plan

Baseline: Takopi 0.23.4, commit e0123d4. Retain Telegram transport,
Codex app-server, engine controls, projects, chat/topic sessions and worktrees.
No Jira integration, host deployment, push, merge or production credentials.

## Sprint 1 (must pass checks before Sprint 2)

1. Ship non-root Docker/Compose with bounded mounts, resource limits, persistent
   config/Codex/project storage and official Codex device login.
2. Refuse Telegram backend startup without numeric sender allowlist; reject all
   unauthorized messages and callbacks before dispatch, log sender IDs only.
3. Add polling heartbeat healthcheck and use existing structured logging.
4. Document Ubuntu setup, credentials separation, backup/restore and smoke test.
5. Run targeted tests and full `just check`; record environment limitations.

## Sprint 2

1. Isolated security module: conservative command/path classifier, single-use
   user/chat/thread/turn/request-bound approvals with TTL and JSONL audit.
2. Integrate asynchronous approvals without blocking the JSON-RPC reader, so
   interrupt, steer and other sessions remain operational. Remove all automatic
   permission grants. Check file-change paths from item notifications.
3. Telegram inline buttons, deny notifications, file-transfer hardening.
4. Convenience commands using existing runtime context and cancel controls.
5. Adversarial tests; full checks; threat model and upstream change inventory.

## Security boundary decision

App-server approvals are requests from Codex, not an OS syscall interceptor.
Use explicit conservative Codex sandbox and approval settings on every turn,
including resumed sessions. An approval must never switch to full access or
approve a session-wide exec-policy amendment. Tests and repository scripts are
executable code, so a shell allowlist alone cannot isolate malicious repositories.
Document these limits rather than claiming a complete security boundary. Host
secrets and Docker socket must never enter the container. Strong tenant/secret
isolation requires a separate worker identity/container in a later sprint.

## Future extension

Use the command entry-point API for `takopi-jira`; PAT stays in a separate proxy.

## Execution record

- Sprint 1 `just check`: 594 passed; coverage 81.44%.
- Sprint 2 initial full check: 709 passed; coverage 81.65%; subsequent hardening
  checks recorded in IMPLEMENTATION_REPORT.md.
- Docker image built; UID 1001 and official Codex 0.163.0 verified without auth.
- Actual app-server initialize and managed-only requirements loading verified in
  isolated container without network/host mounts/secrets.
- Default Docker profile on this host prevents Bubblewrap user namespaces.
  Landlock compatibility flag did not provide a working alternative. Startup
  preflight now fails closed. No privileged/unconfined fallback was added.
- Real Telegram/Codex-login/VPS restart milestone remains an operator acceptance
  test; no external deployment or production access performed.

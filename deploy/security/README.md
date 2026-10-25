# Codex sandbox compatibility profiles

Validated on Linux amd64, Ubuntu 24.04 kernel 6.8.0-146-generic, Docker 29.1.3,
Codex 0.163.0. These profiles are opt-in through ../compose.sandbox.yaml.
The base production Compose remains unchanged. Never use privileged,
SYS_ADMIN, seccomp=unconfined, apparmor=unconfined or sandbox bypass for the app.

## Source and policy changes

Both profiles derive from the Moby Authors' Apache-2.0 policies at commit
`4b8214fc4971e06172938a1f1554ccff6647ee80`:

- https://github.com/moby/profiles/blob/4b8214fc4971e06172938a1f1554ccff6647ee80/seccomp/default.json
  (upstream file SHA256: `6416b47770785a41ac59073cdc77d9fe98517df2799dc83ef207e622de3053f6`);
- https://github.com/moby/profiles/blob/4b8214fc4971e06172938a1f1554ccff6647ee80/apparmor/template.go.

See LICENSE.moby. The JSON preserves the upstream rules and default-deny action,
then appends three rules:

1. unshare accepts only a subset of USER, MOUNT, IPC, PID and NETWORK namespace
   bits (0x78020000); all other flags are rejected.
2. clone permits the namespaces used by bwrap, but keeps UTS/CGROUP bits rejected.
   This addition is restricted to amd64, whose flags argument is argument zero.
   clone3 retains upstream ENOSYS so libc uses filtered clone(2).
3. mount, umount2, pivot_root and chroot are allowed by seccomp. Kernel capability
   checks still prevent their use outside a newly owned user namespace.

The AppArmor template is rendered as the distinct takopi-codex profile. It keeps
Docker's proc/sys/kernel, crypto/vsock, signal and ptrace restrictions. Changes:
ABI 4.0 with explicit network unix, userns permission, mount/pivot_root permission
instead of the default blanket mount denial. No existing profile is overwritten.

These are explicit additions to the outer syscall/mount policy, not identical
Docker defaults. They expose the kernel's unprivileged namespace machinery so
Codex's inner filesystem/network sandbox can actually run. Keep the kernel
patched and retain the trusted-project/same-UID limitations in SECURITY.md.
This does not isolate hostile tenants or conceal same-UID credentials.
App UID/GID 1001, zero capabilities, no-new-privileges, read-only rootfs, bounded
mounts/resources, approval gateway and the Codex sandbox stay enabled.

## Install

Copy compose.sandbox.yaml beside /srv/takopi/compose.yaml; copy security/ here,
scripts/install-sandbox-profile.sh and scripts/check-sandbox.py to /srv/takopi.

A host administrator runs:

```bash
bash /srv/takopi/install-sandbox-profile.sh
```

On the deployed VPS, deploy has Docker administrator access but no sudo.
Its explicit alternative is a one-time trusted administrative loader:

```bash
TAKOPI_SETUP_IMAGE=ghcr.io/mborodako-maker/takopi-agent:sha-99f68e6 \
  bash /srv/takopi/install-sandbox-profile.sh --via-docker
```

The host parser first validates/compiles the profile without loading it. The loader
uses only MAC_ADMIN, no network, read-only rootfs and no-new-privileges. It mounts
compiled/public policy inputs, the policy control filesystem and /etc/apparmor.d
for installing the single named policy. It does not mount credentials, Docker
socket or project data. This is an administrative operation, not an application
service or capability granted to the bot. The image must already be downloaded.

The installed /etc/apparmor.d/takopi-codex is root-owned, mode 0644 and loaded in
enforce mode. AppArmor loads this file on boot; Docker/AppArmor are not restarted.
Rebooting the production VPS was deliberately not part of this verification.

Add to /srv/takopi/.env (preserve other settings):

```dotenv
COMPOSE_FILE=compose.yaml:compose.sandbox.yaml
```

Then, with the required Telegram settings supplied:

```bash
cd /srv/takopi
docker compose config --quiet
docker compose run --rm --no-deps -T takopi /usr/local/bin/sandbox-check.sh
docker compose run --rm --no-deps -T takopi python - < check-sandbox.py
```

The checks need no Codex account. For an initial credentials-free preflight only,
pass non-secret placeholders for the mandatory Compose Telegram variables;
never use them to start the bot. Actual bot startup requires real credentials.

The enforcement check verifies app UID/capabilities/seccomp/AppArmor, a writable
project, denied writes to a sibling directory and Codex auth directory, and denied
IP socket creation inside Codex after confirming the outer process can create
such sockets. It creates and removes only its own temporary workspace directory.

## Rollback

Stop Takopi before changing its profiles. Remove the COMPOSE_FILE override or
explicitly select compose.yaml. The default profile again fails preflight on
this host; startup remains fail-closed. Keep data/auth directories and backups.
The unused named AppArmor profile can remain loaded; no other service uses it.
An administrator can unload it after stopping all Takopi containers if needed.

## Verification record (2026-10-25)

- Target VPS preflight passed through the real production Compose/overlay.
- Standalone enforcement passed: project writes allowed; sibling and Codex-auth
  writes denied; IP sockets denied inside Codex; UID 1001 and CapEff=0 verified.
- Named AppArmor profile persisted root-owned at /etc/apparmor.d/takopi-codex.
  No host reboot or Docker/AppArmor restart was performed.
- Full just check: 802 passed, coverage 81.86%; Ruff/ty passed with five existing
  ty warnings. 28 new profile tests passed, including namespace-flag filters,
  sensitive syscall denials, Compose merging and installer fail-fast behavior.
- actionlint, shell syntax, and diff checks passed.
- The application image remains sha-99f68e6: only external runtime policy assets
  and diagnostics were added; application source/image code was not changed.

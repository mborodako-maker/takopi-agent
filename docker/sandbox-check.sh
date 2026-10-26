#!/bin/sh
set -eu
# Do not start a bot that would repeatedly request sandbox escapes for ordinary
# work. This command needs no login and must succeed under the real Docker profile.
if ! env -i HOME="$HOME" PATH="$PATH" CODEX_HOME="${CODEX_HOME:-$HOME/.codex}" \
    LANG="${LANG:-C.UTF-8}" codex sandbox -c 'sandbox_mode="workspace-write"' \
    -c 'sandbox_workspace_write.network_access=false' -- /bin/true >/dev/null 2>&1; then
    echo 'Codex sandbox unavailable under this host/container security profile; refusing bot startup. See SECURITY.md.' >&2
    exit 1
fi

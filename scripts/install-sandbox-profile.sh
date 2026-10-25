#!/usr/bin/env bash
# Install only the named Takopi AppArmor policy; never restart Docker/AppArmor.
set -euo pipefail
case "${1:-}" in
    ''|--via-docker) ;;
    *) echo 'Usage: install-sandbox-profile.sh [--via-docker]' >&2; exit 2 ;;
esac
[[ $# -le 1 ]] || { echo 'Too many arguments' >&2; exit 2; }
runtime_dir=${TAKOPI_RUNTIME_DIR:-/srv/takopi}
profile_source="$runtime_dir/security/apparmor.profile"
[[ $(uname -m) = x86_64 ]] || { echo 'This compatibility profile is validated for Linux amd64 only.' >&2; exit 1; }
command -v apparmor_parser >/dev/null
[[ -f $profile_source ]] || { echo "Missing profile: $profile_source" >&2; exit 1; }
if [[ ${1:-} != --via-docker ]]; then
    [[ $EUID = 0 ]] || { echo 'Run as host administrator, or explicitly choose --via-docker with Docker administrator access.' >&2; exit 1; }
    apparmor_parser -Q -T "$profile_source"
    install -o 0 -g 0 -m 0644 "$profile_source" /etc/apparmor.d/takopi-codex
    apparmor_parser -r -T /etc/apparmor.d/takopi-codex
    exit
fi
: "${TAKOPI_SETUP_IMAGE:?Set the already downloaded trusted Takopi image for the one-time administrative loader}"
compiled=$(mktemp /tmp/takopi-apparmor.XXXXXX)
trap 'rm -f -- "$compiled"' EXIT
apparmor_parser -Q -T -o "$compiled" "$profile_source"
# These files contain public policy only. Credentials are never mounted here.
chmod 0644 "$compiled" "$profile_source"
docker run --rm -i --pull never --user 0:0 --network none --read-only \
    --cap-drop ALL --cap-add MAC_ADMIN --security-opt no-new-privileges \
    --memory 128m --pids-limit 64 \
    --mount type=bind,src=/sys/kernel/security,dst=/policyfs \
    --mount type=bind,src=/etc/apparmor.d,dst=/host-profiles \
    --mount "type=bind,src=$profile_source,dst=/profile-source,readonly" \
    --mount "type=bind,src=$compiled,dst=/compiled,readonly" \
    --entrypoint python "$TAKOPI_SETUP_IMAGE" - <<'PY'
from pathlib import Path
import os

source = Path('/profile-source').read_bytes()
if b'profile takopi-codex flags=' not in source:
    raise SystemExit('Unexpected policy name')
blob = Path('/compiled').read_bytes()
fd = os.open('/policyfs/apparmor/.replace', os.O_WRONLY)
try:
    if os.write(fd, blob) != len(blob):
        raise RuntimeError('Incomplete AppArmor policy load')
finally:
    os.close(fd)
target = Path('/host-profiles/takopi-codex')
if target.exists() and target.read_bytes() != source:
    backup = target.with_name('.takopi-codex.previous')
    backup.write_bytes(target.read_bytes())
    backup.chmod(0o600)
temporary = target.with_name('.takopi-codex.new')
with temporary.open('wb') as file:
    file.write(source)
    file.flush()
    os.fsync(file.fileno())
temporary.chmod(0o644)
os.replace(temporary, target)
print('Installed root-owned /etc/apparmor.d/takopi-codex (enforce); no service restart')
PY

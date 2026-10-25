#!/bin/sh
set -eu
umask 077
if [ "$(id -u)" = 0 ]; then
    echo 'Refusing to run as root' >&2
    exit 1
fi
# Login/maintenance commands do not require a Telegram credential.
if [ "${1:-}" = takopi ]; then
    : "${TELEGRAM_BOT_TOKEN:?Telegram bot token is required}"
    : "${TELEGRAM_ALLOWED_USER_IDS:?Numeric Telegram allowlist is required}"
    : "${TELEGRAM_CHAT_ID:?Telegram chat ID is required}"
    export TAKOPI__TRANSPORTS__TELEGRAM__BOT_TOKEN="$TELEGRAM_BOT_TOKEN"
    export TAKOPI__TRANSPORTS__TELEGRAM__CHAT_ID="$TELEGRAM_CHAT_ID"
    TAKOPI__TRANSPORTS__TELEGRAM__ALLOWED_USER_IDS=$(python -c '
import json, os
values = [int(v.strip()) for v in os.environ["TELEGRAM_ALLOWED_USER_IDS"].split(",")]
if not values or any(v <= 0 for v in values):
    raise SystemExit("Telegram user IDs must be positive integers")
print(json.dumps(values))')
    export TAKOPI__TRANSPORTS__TELEGRAM__ALLOWED_USER_IDS
    unset TELEGRAM_BOT_TOKEN TELEGRAM_ALLOWED_USER_IDS TELEGRAM_CHAT_ID
    rm -f "${TAKOPI_HEALTH_FILE:-/tmp/takopi-polling}"
    /usr/local/bin/sandbox-check.sh
else
    unset TELEGRAM_BOT_TOKEN TELEGRAM_ALLOWED_USER_IDS TELEGRAM_CHAT_ID
    unset TAKOPI__TRANSPORTS__TELEGRAM__BOT_TOKEN
fi
exec "$@"

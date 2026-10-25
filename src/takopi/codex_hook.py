"""Managed Codex hook: every error becomes an explicit blocking decision."""

from __future__ import annotations

import json
import os
import socket
import sys


def main() -> None:
    allowed = False
    event = "PreToolUse"
    try:
        raw = sys.stdin.buffer.read(1024 * 1024)
        data = json.loads(raw)
        event = data.get("hook_event_name", event)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(150)
            client.connect(
                os.environ.get("TAKOPI_APPROVAL_SOCKET", "/tmp/takopi-approval.sock")
            )
            client.sendall(json.dumps(data).encode() + b"\n")
            with client.makefile("rb") as response:
                allowed = json.loads(response.readline(4096)).get("allow") is True
    except Exception:  # noqa: BLE001
        allowed = False
    if event == "PostToolUse":
        print("{}")
    else:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "allow" if allowed else "deny",
                        "permissionDecisionReason": "Takopi Approval Gateway"
                        if allowed
                        else "Blocked by Takopi security policy or unavailable gateway",
                    }
                }
            )
        )


if __name__ == "__main__":
    main()

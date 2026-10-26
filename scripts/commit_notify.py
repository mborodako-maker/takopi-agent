# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "mistune>=3.2.0",
#     "requests>=2.32.5",
#     "sulguk>=0.11.1",
# ]
# ///
from __future__ import annotations

import json
import os
from pathlib import Path
import re


def main() -> int:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        print(
            "::notice::Telegram commit notification skipped: configure "
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in repository Actions secrets."
        )
        return 0

    import mistune
    import requests
    import sulguk

    repo = os.environ["REPO"]

    event_path = Path(os.environ["GITHUB_EVENT_PATH"])
    event = json.loads(event_path.read_text(encoding="utf-8"))

    ref = event.get("ref") or ""
    branch = ref.removeprefix("refs/heads/") if ref.startswith("refs/heads/") else ref
    commits = list(event.get("commits") or [])
    head_commit = event.get("head_commit")
    if not commits and head_commit:
        commits = [head_commit]

    PULL_RE = re.compile(rf"(https://github.com/{repo}/pull/(\d+))")
    PULL_NUM_RE = re.compile(r"\(#(\d+)\)")

    def _short_sha(value: str) -> str:
        return value[:7] if value else ""

    def _commit_line(commit: dict[str, object]) -> str:
        full_sha = str(commit.get("id") or "")
        sha = _short_sha(full_sha)
        message = str(commit.get("message") or "").splitlines()[0].strip()
        message = PULL_RE.sub(r"[#\2](\1)", message)
        message = PULL_NUM_RE.sub(
            rf"([#\1](https://github.com/{repo}/pull/\1))", message
        )
        url = f"https://github.com/{repo}/commit/{full_sha or sha}"
        return f"- [{sha}]({url}) {message}"

    lines: list[str] = []
    if commits:
        max_commits = 10
        lines.extend(_commit_line(commit) for commit in commits[:max_commits])
        if len(commits) > max_commits:
            lines.append(f"- ...and {len(commits) - max_commits} more")

    header = f"push **{repo} {branch}**".strip()
    parts = [header]
    if lines:
        parts.append("\n".join(lines))

    message = "\n\n".join(part for part in parts if part)

    html = mistune.html(message)
    rendered = sulguk.transform_html(html)

    payload = {
        "chat_id": chat_id,
        "text": rendered.text,
        "entities": rendered.entities,
        "link_preview_options": {"is_disabled": True},
    }
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
    except requests.RequestException:
        # Request exception messages contain the URL, including the bot token.
        print(
            "::error::Telegram commit notification failed; check Actions secrets and bot access to the chat."
        )
        return 1
    print("Telegram commit notification sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

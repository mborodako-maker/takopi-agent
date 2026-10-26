import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/commit_notify.py"


@pytest.mark.parametrize(
    ("token", "chat"), [(None, None), ("   ", "-100"), ("123:fixture", "")]
)
def test_missing_notification_secrets_skip_before_loading_event(
    monkeypatch, capsys, token, chat
):
    for name, value in (("TELEGRAM_BOT_TOKEN", token), ("TELEGRAM_CHAT_ID", chat)):
        monkeypatch.delenv(name, raising=False)
        if value is not None:
            monkeypatch.setenv(name, value)
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    main = runpy.run_path(str(SCRIPT))["main"]
    assert main() == 0
    assert "notification skipped" in capsys.readouterr().out


@pytest.mark.parametrize("failure", [False, True])
def test_configured_notification_uses_timeout_and_redacts_errors(
    monkeypatch, tmp_path, capsys, failure
):
    token = "123:fixture-private-token"
    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
    request_error = type("RequestException", (Exception,), {})
    response = Mock()
    if failure:
        response.raise_for_status.side_effect = request_error(endpoint)
    post = Mock(return_value=response)
    monkeypatch.setitem(
        sys.modules,
        "requests",
        SimpleNamespace(post=post, RequestException=request_error),
    )
    monkeypatch.setitem(sys.modules, "mistune", SimpleNamespace(html=lambda text: text))
    monkeypatch.setitem(
        sys.modules,
        "sulguk",
        SimpleNamespace(
            transform_html=lambda text: SimpleNamespace(text=text, entities=[])
        ),
    )
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps(
            {
                "ref": "refs/heads/master",
                "head_commit": {"id": "abcdef012345", "message": "fixture commit"},
            }
        )
    )
    for name, value in {
        "REPO": "fixture/repo",
        "TELEGRAM_BOT_TOKEN": token,
        "TELEGRAM_CHAT_ID": "-100",
        "GITHUB_EVENT_PATH": str(event),
    }.items():
        monkeypatch.setenv(name, value)
    main = runpy.run_path(str(SCRIPT))["main"]
    assert main() == (1 if failure else 0)
    assert post.call_args.args == (endpoint,)
    assert post.call_args.kwargs["timeout"] == 30
    assert post.call_args.kwargs["json"]["chat_id"] == "-100"
    assert "abcdef0" in post.call_args.kwargs["json"]["text"]
    output = capsys.readouterr()
    assert token not in output.out + output.err
    assert ("::error::" in output.out) is failure

"""The complete, deliberately small environment inherited by Codex processes."""

from __future__ import annotations

import os
from collections.abc import Mapping

CODEX_ENV_KEYS = frozenset(
    {
        "HOME",
        "PATH",
        "USER",
        "LOGNAME",
        "LANG",
        "LC_ALL",
        "CODEX_HOME",
        "TAKOPI_APPROVAL_SOCKET",
    }
)


def safe_codex_env(parent: Mapping[str, str] | None = None) -> dict[str, str]:
    """Use persisted Codex login; API keys, proxies and arbitrary env are absent.

    TAKOPI_APPROVAL_SOCKET is needed by the managed approval hook; its value
    comes from trusted application configuration, never Telegram task arguments.
    Filesystem isolation is still required: an environment filter cannot hide
    readable .env files or credentials mounted inside the worker container.
    """
    source = os.environ if parent is None else parent
    return {key: value for key, value in source.items() if key in CODEX_ENV_KEYS}

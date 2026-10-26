# Trader repository

Trader is maintained separately at https://github.com/mborodako-maker/trader.
Register its checkout as a Takopi project at `/workspace/trader`; it is not a
Python dependency of the bridge and is not copied into the bridge image.

See trader's `docs/GITHUB_INTEGRATION.md` for checkout, project configuration,
skills and separate SHADOW worker setup. Existing whitelist, managed hooks,
approval gateway and sandbox remain required for maintenance commands.

`deploy/compose.trader.yaml` optionally adds a read-only state-directory mount
and `TAKOPI_TRADER_DB`. Apply it alongside the installed production and sandbox
Compose files after preparing the state directory and read permissions for UID
1001. Mount only SQLite state, never the worker's Codex home or credentials.
No worker or automatic VPS deployment is started by this overlay.

The Docker workflow tests pull requests and publishes on pushes to master/main,
version tags or manual dispatch after its test job succeeds.

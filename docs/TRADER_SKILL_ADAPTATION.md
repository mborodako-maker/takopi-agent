# Crypto research boundary

The operator confirmed no trading service previously existed. Takopi remains the
Telegram/Codex bridge. The separate crypto-trader-shadow project supplies public
Bybit data, indicators, screening, structured proposals, deterministic sizing,
RiskEngine/SHA-256 fingerprints, and SHADOW/PAPER virtual tracking.

Six skills: crypto-screener, crypto-strategist, crypto-position-sizer,
crypto-trader-risk, crypto-news, crypto-alerts. Install maintained skill folders
under `${CODEX_HOME:-$HOME/.codex}/skills` for the actual worker user. Supplied
Docker defaults: `/home/codex/.codex`, UID/GID 1001; verify the actual VPS mounts.

Jakeschincariol/trader-skill was used as reference for risk gating, exact-order
fingerprints, separate sizing, numeric filters and persistent news/alert state.
No code was transplanted. Alpaca/stock/NYSE/SEC/Claude connector assumptions were
rejected. No authenticated exchange write operation is connected to Takopi.

Use read-only views described in CODEX_TASK_RUNNER.md. Maintenance task completion
is not a trading approval. Future live execution needs a separate trusted service.

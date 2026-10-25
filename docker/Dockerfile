FROM python:3.14-slim-bookworm
ARG CODEX_VERSION=0.163.0
# Official OpenAI CLI. Pin deliberately; validate upgrades before changing this.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git nodejs npm tini ripgrep \
    && npm install -g @openai/codex@${CODEX_VERSION} \
    && npm cache clean --force \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1001 codex && useradd --uid 1001 --gid 1001 --create-home codex
WORKDIR /opt/takopi
COPY pyproject.toml uv.lock readme.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir uv==0.9.18 \
    && UV_CACHE_DIR=/tmp/uv-cache uv sync --frozen --no-dev \
    && rm -rf /tmp/uv-cache
ENV PATH="/opt/takopi/.venv/bin:$PATH"
COPY docker/codex-requirements.toml /etc/codex/requirements.toml
COPY docker/entrypoint.sh docker/healthcheck.sh docker/sandbox-check.sh /usr/local/bin/
RUN chmod 755 /usr/local/bin/entrypoint.sh /usr/local/bin/healthcheck.sh /usr/local/bin/sandbox-check.sh \
    && mkdir -p /workspace /home/codex/.codex /home/codex/.takopi /var/log/takopi \
    && chown -R 1001:1001 /workspace /home/codex /var/log/takopi
ENV HOME=/home/codex CODEX_HOME=/home/codex/.codex \
    TAKOPI_LOG_FORMAT=json TAKOPI_LOG_FILE=/var/log/takopi/application.jsonl \
    TAKOPI_HEALTH_FILE=/tmp/takopi-polling TAKOPI_APPROVAL_SOCKET=/tmp/takopi-approval.sock \
    TAKOPI_AUDIT_LOG=/var/log/takopi/audit.jsonl TAKOPI_WORKSPACE_ROOT=/workspace
USER 1001:1001
WORKDIR /workspace
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["takopi"]

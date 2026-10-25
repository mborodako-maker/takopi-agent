# Локальная разработка takopi-agent

Исходники bridge находятся на компьютере разработчика. Рабочие repositories
Codex в `/workspace` — отдельные проекты. VPS получает только готовый image
из GHCR и runtime-файлы из `deploy/`, без checkout исходников bridge.

## Python и проверки

Нужны Python 3.14, uv, just и Docker Compose v2 (2.20+ для include).
Deployment tests используют Compose CLI без обращения к Docker daemon:

```bash
git clone <URL-ВАШЕГО-ФОРКА> takopi-agent
cd takopi-agent
uv sync --frozen
just check
```

`just check` выполняет Ruff format/lint, ty и весь pytest с coverage gate 81%,
включая security и deployment tests. Для отдельной проверки:

```bash
uv run pytest tests/test_deployment.py tests/test_vps_scripts.py --no-cov
uv run pytest tests/test_vps_security.py tests/test_vps_commands.py --no-cov
```

Unix socket integration tests требуют среды, разрешающей bind Unix sockets.
Их нельзя отключать ради успешного результата.

## Локальный контейнер

```bash
bash scripts/install.sh
```

Этот скрипт создаёт `config/runtime`, `workspace`, `logs`. Установите владельца
UID/GID 1001 для этих каталогов административными средствами, если ваш UID иной.
Настройте `config/runtime/takopi.toml` и добавьте рабочие Git repositories в
`workspace/`. Храните Telegram credentials вне checkout и workspace, например
в `$HOME/.config/takopi-dev.env` с правами 0600 по шаблону `.env.example`.

```bash
export COMPOSE_ENV_FILES="$HOME/.config/takopi-dev.env"
docker compose -f compose.dev.yaml build
docker compose -f compose.dev.yaml run --rm takopi /usr/local/bin/sandbox-check.sh
docker compose -f compose.dev.yaml run --rm takopi codex login --device-auth
docker compose -f compose.dev.yaml up --build
```

После изменения исходников пересоберите контейнер. `docker-compose.yml` —
совместимый локальный entry point, подключающий `compose.dev.yaml`.
Persistent auth в local named volume сохраняется при recreation; не удаляйте его.
Production использует отдельные bind directories и `deploy/compose.yaml`.

Docker security profile сохранён: non-root, read-only rootfs, cap_drop ALL,
no-new-privileges. Если sandbox preflight не проходит, исправьте совместимость
хоста; не отключайте sandbox/seccomp/AppArmor и не добавляйте capabilities.
Runtime содержит Python/Node/Git; дополнительные SDK добавляются в Dockerfile
локально и собираются GitHub Actions, затем доставляются через GHCR.

## Публикация

Push в `main`, tag `v*` или ручной запуск workflow `Docker` запускает `just check`,
проверки Compose и только затем Buildx build/publish. Никакого SSH deployment.
Для production выберите существующий `v*` или `sha-*` tag, см. README_VPS.md.
Изменения кода, lockfile и Dockerfile делаются здесь, а не на VPS.

Для Linux-хостов с запрещённым nested user namespace используйте явно
установленные профили по deploy/security/README.md. Enforcement-проверка
scripts/check-sandbox.py предназначена для контейнера с этим overlay.

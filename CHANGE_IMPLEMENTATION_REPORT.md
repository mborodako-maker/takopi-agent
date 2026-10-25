# CHANGE IMPLEMENTATION REPORT

## 1. Что сохранено

Продолжена реализация из commits `449ca12` и `a740e21`, без повторной разработки
Sprint 1/2. Весь `src/` остался без изменений: Telegram/Codex integration,
sessions, worktrees, gateway, classifier, audit, path validation, allowlist.
Все 759 прежних tests сохранены и прошли вместе с новыми.

## 2. Что изменено

Разделены локальная разработка/сборка и source-free production runtime.
Production Compose получает готовый image из GHCR, требует явные repository/tag
и монтирует persistent workspace/config/auth/state/logs. `takopi.toml` read-only,
соседние state-файлы writable. Корневой Compose включает dev-конфигурацию.
Backup поддерживает выбранный runtime root с прежней канонической проверкой
назначения; production update больше не выполняет git pull или build.

## 3. Новые файлы

- `compose.dev.yaml`;
- `deploy/compose.yaml`, `deploy/.env.example`;
- `.github/workflows/docker.yml`;
- `scripts/update-vps.sh`;
- `README_LOCAL_DEV.md`;
- `tests/test_deployment.py`;
- `CHANGE_IMPLEMENTATION_REPORT.md`.

## 4. Изменённые файлы

- `docker-compose.yml`;
- `scripts/install.sh`, `scripts/update.sh`, `scripts/backup.sh`;
- `.env.example`, `config/takopi.toml.example`;
- `pyproject.toml`, `uv.lock`: PyYAML явно добавлен в dev-зависимости для YAML tests,
  версии runtime dependencies не менялись;
- `tests/test_vps_scripts.py`;
- `README_VPS.md`, `SECURITY.md`, `docs/IMPLEMENTATION_PLAN.md`,
  `docs/UPSTREAM_CHANGES.md`, `IMPLEMENTATION_REPORT.md`.

Пользовательские CODEX_TASK.md и CODEX_TASK_NEW.md не изменялись и не включены
в commit. Старый implementation report помечен как исторический.

## 5. Local development

Clone выполняется только на компьютере разработчика. `uv sync --frozen`,
`just check`; container development — `docker compose -f compose.dev.yaml up --build`.
Credentials лежат вне checkout/workspace. Local auth остаётся в named volume.
Исходники изменяются локально, контейнер пересобирается после правок.

## 6. GitHub Actions

Workflow Docker запускается на push в main, tags v* и workflow_dispatch.
Job tests выполняет frozen dependency installation и полный just check,
включая security/deployment tests, затем Compose/shell validation.
Job publish имеет needs: tests; при ошибке проверки build/publish не запускается.
SSH deployment отсутствует.

## 7. GHCR

Buildx собирает docker/Dockerfile после проверок. Login использует GITHUB_TOKEN
с packages:write только в publish job; остальные permissions — contents:read.
Image: ghcr.io/<lowercase-owner>/takopi-agent. Tags: latest только для main,
sha-<short-sha> для каждого запуска и исходный v* tag для tag push.
Cache: GitHub Actions cache (cache-from/cache-to type=gha).

## 8. Production VPS

На VPS передаются только runtime assets, без исходников bridge:
/srv/takopi/{compose.yaml,.env,config/takopi.toml,data/{codex,takopi,logs},workspace}.
Workspace содержит отдельные Git repositories Codex. Container paths прежние.
Production Compose не содержит build/source mounts. Создание mount sources
автоматически запрещено. Оператор создаёт их с владельцем UID/GID 1001.
После pull обязателен sandbox preflight до Codex device login/start.
Private GHCR image требует read:packages login администратора на host;
registry credentials не передаются worker. Auth/sessions переживают recreation.

## 9. Update

Изменить IMAGE_TAG на проверенный опубликованный tag, предварительно сохранить
backup и прошлую версию. `bash /srv/takopi/update-vps.sh` выполняет
compose pull → up -d --no-build → ps из runtime root. При ошибке pull прекращает
работу до up. Старый scripts/update.sh делегирует новому entry point.
TAKOPI_RUNTIME_DIR поддерживает альтернативный путь. Automatic backup больше
не входит в update: он отдельно описан в README_VPS.md и выполняется до update.

## 10. Rollback

Вернуть предыдущий IMAGE_TAG, выполнить pull и up -d --no-build. Persistent
данные не удаляются. Image rollback не отменяет миграции данных: при
несовместимом state нужен backup совместимой версии. README_VPS.md содержит
backup/restore и перенос из прежнего named volume в новые bind directories.

## 11. Security controls

Сохранены UID/GID 1001, cap_drop ALL, no-new-privileges, read-only rootfs,
resource/tmpfs/PID limits, allowlist, AUTO_ALLOW/REQUIRE_APPROVAL/DENY,
одноразовые approvals/TTL/revocation, audit и secret/traversal/symlink protection.
Docker socket, host home, production secrets и source checkout не монтируются.
Mandatory Codex sandbox preflight и отсутствие небезопасного fallback сохранены.

## 12. Выполненные проверки

- Полный just check: Ruff format/lint, ty, весь pytest с coverage gate 81%.
- 15 добавленных regression cases: production mounts/security, dev separation,
  tests-before-publish/token scope/cache/tags, update sequence/fail-fast через оба
  entry points, реальный Compose interpolation версий/обязательных переменных,
  canonical backup destinations при внешнем runtime root.
- `docker compose config --quiet` для production, dev и совместимого root Compose
  с placeholder credentials (daemon не нужен).
- actionlint 1.7.7 для нового workflow: YAML, Actions schema/expressions.
- bash -n для install/update/update-vps/backup scripts; git diff --check.

Полный check выполнялся вне development sandbox: для Unix socket integration
нужен bind(), а фактический Project/project path блокировал sandbox-запись.
Ни один тест не отключён; production security profile не ослаблялся.

## 13. Результат

774 passed; coverage 81.85%; Ruff format/lint прошли. Ty завершился успешно
с пятью прежними warnings. Compose, actionlint, shell syntax и diff checks прошли.
Новых failures нет. Runtime source diff отсутствует.

## 14. Ограничения

GitHub workflow не запускался на удалённом GitHub, image не публиковался,
push/tag/release/SSH deployment не выполнялись. Первую сборку и GHCR publication
проверяет CI после push; repository/package permissions должны разрешать
GITHUB_TOKEN публикацию. IMAGE_REPOSITORY в production надо заменить на свой.
Новая container build/runtime проверка в этой задаче не выполнялась; ранее
зафиксированные Docker/filesystem и Bubblewrap compatibility ограничения не
считаются устранёнными. Требуется совместимый target VPS и account-backed
Telegram/login/restart milestone, а также restore drill. Same-UID confidentiality
и hostile-repository ограничения Sprint 2 сохраняются (SECURITY.md).

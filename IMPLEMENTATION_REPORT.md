# IMPLEMENTATION REPORT

Исторический отчёт Sprint 1/2 и security audit. Deployment впоследствии изменён:
актуальная схема GHCR/VPS описана в README_VPS.md, README_LOCAL_DEV.md
и CHANGE_IMPLEMENTATION_REPORT.md; старые инструкции clone/build на VPS ниже
сохранены только как запись предыдущего этапа.

## 1. Что изменено

Подготовлены Docker/VPS профиль и Approval Gateway поверх Takopi 0.23.4.
Сохранены официальный Codex app-server, стандартные projects/worktrees,
streaming, chat/topic sessions, resume, steer и interrupt. Jira не реализована,
внешний deployment, push и merge не выполнялись.

Повторный аудит текущего задания проверил уже существующий commit `449ca12`
и устранил дополнительные обходы DENY через cwd/кавычки/слитные опции,
непроверенные move destinations и автоматическое разрешение удаления в
file-change requests. Backup теперь проверяет канонический путь назначения;
container sandbox preflight перенесён непосредственно перед login в README.

Это проверенная реализация с известными границами, **не подтверждённый production
milestone на VPS**. Текущий Docker-хост не предоставляет необходимые Bubblewrap
namespaces при заданном профиле безопасности. Startup прекращается, если sandbox
не работает. Полная защита секретов от произвольного same-UID кода также требует
следующего архитектурного этапа (см. пункт 8).

## 2. Созданные файлы

- `docker/Dockerfile`, `entrypoint.sh`, `healthcheck.sh`, `sandbox-check.sh`,
  `codex-requirements.toml`;
- `docker-compose.yml`, `.dockerignore`, `.env.example`;
- `config/takopi.toml.example`;
- `scripts/install.sh`, `backup.sh`, `update.sh`;
- `src/takopi/security.py`, `codex_hook.py`, `telegram/approval.py`,
  `telegram/commands/vps.py`;
- `tests/test_vps_security.py`, `tests/test_vps_commands.py`,
  `tests/test_vps_scripts.py` (добавлен при повторном аудите);
- `README_VPS.md`, `SECURITY.md`, `docs/IMPLEMENTATION_PLAN.md`,
  `docs/UPSTREAM_CHANGES.md`, `docs/JIRA_PLUGIN_DESIGN.md`, этот отчёт.

Каталоги workspace/logs/runtime создаёт install script; содержимое игнорируется Git
и не входит в Docker build context. Существующие пользовательские `CODEX_TASK*.md`
не изменялись и не включены в commit реализации.

## 3. Изменённые Takopi core файлы

`config.py`, `ids.py`, `logging.py`, `runner_bridge.py`, `runners/codex.py`,
`settings.py`, `transport_runtime.py`, `utils/git.py`, `worktrees.py`;
`telegram/backend.py`, `bridge.py`, `loop.py`, `parsing.py`, `files.py`,
`telegram/commands/executor.py`, `file_transfer.py`, `menu.py`.

Причины, plugin feasibility и места будущих conflicts перечислены в
`docs/UPSTREAM_CHANGES.md`. Отдельные новые модули содержат основную логику;
управление worktrees не переписывалось.

## 4. Security controls

UID/GID 1001, cap_drop ALL, no-new-privileges, read-only rootfs, bounded tmpfs/PIDs,
конфигурируемые CPU/memory, только четыре разрешённых storage mounts. Официальный
Codex 0.163.0 и frozen Python lockfile. Секретный env вне workspace/checkout,
Codex auth/sessions в отдельном volume; Telegram credentials удаляются из env
Codex child и maintenance commands.

Обязательный numeric allowlist на startup и перед всеми сообщениями/callbacks;
пустой список не означает allow-all. Structured startup/shutdown/selection/
worktree/session/approval logs, redaction, count-only Telegram polling logs.

Центральные AUTO_ALLOW/REQUIRE_APPROVAL/DENY; async JSON-RPC approvals; mandatory
managed PreToolUse/PostToolUse hooks; random single-use user/chat/request/turn/
command-bound capabilities, TTL с учётом доставки, отмена на completion/interrupt/
disconnect/serverRequest resolution; никаких session-wide permission grants.
JSONL audit. Telegram deny message и confirmation buttons.

Resolved paths для project/worktree, проверка workspace boundary и symlinks,
mandatory sensitive-file deny rules, configurable 20 MiB transfer limit.
`/projects`, `/status`, `/abort`, `/diff`, `/tests` с config-only argv и Codex
sandbox, `/commit` с staged preview, confirmation и проверкой index/HEAD/branch.
Bridge Git operations отключают repository hooks/fsmonitor.

## 5. Выполненные проверки

- `just check`: Ruff format, Ruff lint, ty, весь pytest с coverage gate;
- allowlist sender/callback/empty startup, command classification и DENY vectors;
- TTL, доставка, wrong user/chat, replay, request/turn revocation;
- actual local Unix socket hook integration, audit redaction, file-change approvals;
- path traversal, symlink escape, mandatory .env/key/auth/credentials filtering;
- дополнительные regression tests: DENY независимо от cwd, shell-decoded sudo/
  rm/docker.sock, attached option paths, reader flags, file-change kind/move/
  deletion/bulk changes, backup destinations через symlink и `..`;
- настоящие временные Git repositories, стандартное создание/reuse worktrees;
- diff с secret exclusions/новыми файлами, configured tests argv, commit confirmation
  и изменение index, upstream turn/interrupt и локальная `/abort`;
- shell syntax, `docker compose config --quiet`, Docker image build;
- финальный image: healthcheck отвергает отсутствующий heartbeat и принимает свежий;
  login/maintenance subprocess не наследует TELEGRAM_BOT_TOKEN;
- isolated container без сети/host mounts/secrets: UID 1001, Codex version,
  app-server initialize, configRequirements/read с реально загруженными managed
  PreToolUse/PostToolUse и featureRequirements.hooks=true;
- Codex Bubblewrap и Landlock compatibility smoke checks под cap_drop ALL /
  no-new-privileges/read-only profile.

Unix socket tests выполнялись с разрешённым выходом из sandbox инструмента:
сам sandbox среды разработки запрещает bind(), поэтому обычный запуск этой
integration проверки внутри него не может пройти. Проверки не отключались.

## 6. Результат

Sprint 1: **594 passed**, coverage **81.44%**.
Baseline повторного аудита: **713 passed**, coverage **81.79%**.
Полный набор после исправлений: **759 passed**, coverage **81.85%**; Ruff/ty прошли.
Пять существующих ty warnings из upstream не являются ошибками; threshold 81%
не уменьшался. Compose validation прошла. Предыдущая сборка image была успешной;
**повторная сборка текущих изменений не завершена**: `npm install` получил
`EROFS: read-only file system`, затем Docker daemon стал недоступен; запуск
хостового `df` также вернул `Input/output error`. Сервис Docker и посторонние
контейнеры не перезапускались. Нельзя считать текущий image проверенным до
восстановления среды и повторного build/smoke test.

Container app-server и managed requirements probe прошли. Sandbox smoke test
**не прошёл из-за запрета user namespaces текущим host/container profile**;
Landlock compatibility flag (включая --no-daemon) не обеспечил альтернативу.
Небезопасный fallback не добавлен. Настоящие Telegram/login/restart acceptance
проверки не выполнялись без целевого VPS и пользовательских accounts.

## 7. Запуск на VPS

Точная Ubuntu 24.04 инструкция: `README_VPS.md`.
Установить Docker Engine/Compose, clone форка, выполнить install script, сохранить
credentials в `/srv/takopi-secrets/takopi.env`, установить `COMPOSE_ENV_FILES`,
настроить config и repos, собрать image. До login проверить
`docker compose run --rm takopi /usr/local/bin/sandbox-check.sh`.
После успешной проверки: `docker compose run --rm takopi codex login --device-auth`,
затем `docker compose up -d --build`. Наблюдать `docker compose logs -f`.
Backup/restore/update и проверка persistence описаны там же.

## 8. Известные ограничения

- Дополнительный blocker текущей среды: EROFS/I/O errors и недоступный Docker
  daemon не позволили закончить повторный build. Сначала восстановить filesystem
  и Docker средствами администратора, затем повторить сборку и container probes.
- Milestone на реальном VPS остаётся открытым до успешного sandbox smoke test,
  Telegram/Codex login и ручной проверки сценария restart. Нельзя устранить
  выявленную несовместимость отключением sandbox/seccomp/AppArmor или privileged.
- Codex hooks не являются полным OS enforcement boundary: есть special tool
  paths, fail-open поведение внутри Codex при ошибке самого hook и уже одобренный
  интерактивный stdin. Наш transport/parsing error выдаёт явный DENY.
- Takopi/Codex используют общий UID; legacy workspace-write sandbox ограничивает
  запись, но имеет broad read access. Test scripts и подтверждённые программы
  могут читать same-UID auth/config, выполнять побочные операции и гонки paths.
  Нельзя считать этот профиль изоляцией от hostile repositories или tenants.
- AUTO_ALLOW для тестов означает доверие к их reviewed source/dependencies.
  Host network egress enforcement здесь не развёрнут. Production credentials,
  DB/SSH/Docker socket отсутствуют, но их нельзя добавлять в этот профиль.
- Redaction не распознаёт произвольно замаскированные секреты в обычном тексте;
  не помещайте их в source/command/output. Opaque inline programs скрываются
  в preview; их следует отклонять, пока действие нельзя осмысленно проверить.
- `/commit` требует staged файлов и Git author config. Не является механизмом
  блокировки внешних параллельных Git операций. Push не выполняется.
- Base image предоставляет Python/Node/Git, остальные project SDK/dependencies
  требуют отдельного reviewed image. Runtime root filesystem read-only.
- Healthcheck не подтверждает auth/account quota и не перезапускает unhealthy
  контейнер автоматически. JSONL logs требуют указанной настройки logrotate.

## 9. Sprint 3

Сначала восстановить Docker build environment (EROFS/I/O), затем решить и проверить sandbox compatibility на целевом VPS без ослабления
контролей. Затем разделить bridge и workers по OS identity/container, передавать
worker только один project/worktree и не монтировать bot config/auth в executable
worker context; credential broker, ограниченный egress proxy, syscall/FS policy,
immutable worker image и тесты реальной атакующей нагрузки. Провести account-backed
Telegram milestone и restore drill. Вынести workflow commands в plugin после
появления public context/control API. Jira — только будущий read-only plugin через
PAT-holding proxy по `docs/JIRA_PLUGIN_DESIGN.md`.

## 10. Upstream conflicts

Наибольший риск — private app-server JSON-RPC lifecycle в `runners/codex.py` и
Telegram dispatch/reload в `telegram/loop.py`. Additive settings/dataclass/command
IDs и file/logging checks — меньшие локальные conflicts. При обновлении сравнить
схему зафиксированной Codex версии, сохранить fail-closed sender checks и revoke
lifecycle, повторить весь `just check`, container requirements probe и VPS milestone.

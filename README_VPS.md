# Production на VPS: готовый image из GHCR

Цепочка: local development → GitHub → GitHub Actions → tests → Docker build →
GHCR → VPS → `docker compose pull` → `docker compose up -d`.
Исходный repository takopi-agent на VPS не нужен. Сборки приложения на VPS нет.
Рабочие Git repositories Codex — отдельные persistent данные в `/workspace`.
Реализация Sprint 1/2, projects/worktrees, sessions и Approval Gateway сохранены.
Ограничения безопасности описаны в SECURITY.md.

## Подготовка

Установите Docker Engine и Compose plugin по
[официальной инструкции Ubuntu](https://docs.docker.com/engine/install/ubuntu/).
Команды установки и работы с Docker выполняет администратор VPS; приложение
всегда работает как UID/GID 1001. Доступ к Docker эквивалентен root на хосте.

Получите из выбранного GitHub release/commit только следующие runtime-файлы
(скачайте через GitHub или передайте с компьютера разработчика):

- `deploy/compose.yaml` → `/srv/takopi/compose.yaml`;
- `deploy/.env.example` → `/srv/takopi/.env`;
- `config/takopi.toml.example` → `/srv/takopi/config/takopi.toml`;
- `scripts/update-vps.sh`, `scripts/backup.sh` → `/srv/takopi/`;
- `SECURITY.md` для ознакомления с границами защиты.

Используйте файлы от той же версии, что и image. Не клонируйте bridge на VPS.
Создайте каталоги от имени администратора до копирования файлов:

```bash
install -d -m 700 /srv/takopi
install -d -m 700 -o 1001 -g 1001 /srv/takopi/config \
  /srv/takopi/data /srv/takopi/data/codex /srv/takopi/data/takopi \
  /srv/takopi/data/logs /srv/takopi/workspace
cd /srv/takopi
```

После копирования задайте права `.env` 0600 (владелец — администратор),
`config/takopi.toml` 0600 и владельца 1001:1001. Структура:

```text
/srv/takopi/
    compose.yaml
    .env
    update-vps.sh
    backup.sh
    config/takopi.toml
    data/codex/
    data/takopi/
    data/logs/
    workspace/
        jira-agent/
        onas/
        fhir-validator/
        cisz/
```

В `.env` задайте опубликованный image и явную версию:

```dotenv
IMAGE_REPOSITORY=ghcr.io/YOUR-GITHUB-OWNER/takopi-agent
IMAGE_TAG=v0.1.0
COMPOSE_PROJECT_NAME=takopi
TELEGRAM_BOT_TOKEN=<из BotFather>
TELEGRAM_ALLOWED_USER_IDS=123456789
TELEGRAM_CHAT_ID=123456789
CONTAINER_MEMORY_LIMIT=4G
CONTAINER_CPU_LIMIT=2.0
TZ=Europe/Minsk
```

GHCR owner пишется lowercase. `v0.1.0` — пример, используйте реально
опубликованный tag. `.env` находится вне workspace и source checkout и не
монтируется в контейнер. Несколько положительных numeric user IDs разделяются
запятыми. Пустой allowlist не запускается; username не используется для доступа.
Не публикуйте вывод `docker compose config`/`inspect`: он содержит credentials.

## Pull, sandbox, проекты и Codex login

Для private image выполните `docker login ghcr.io -u YOUR-GITHUB-USER`, введя
read-only registry token с `read:packages` по запросу. Не помещайте его в `.env`,
workspace или container; registry credentials остаются у администратора хоста.
Для public image login не нужен. Публикация workflow использует `GITHUB_TOKEN`.

```bash
cd /srv/takopi
docker compose config --quiet
docker compose pull
docker compose run --rm takopi /usr/local/bin/sandbox-check.sh
```

Preflight обязателен до login/start. Если preflight не прошёл, не запускайте бот: Docker default profile может
запрещать Bubblewrap namespaces. Landlock compatibility flag
не обеспечил альтернативу. На целевом VPS нужна совместимость Codex с user
namespaces, seccomp и AppArmor. Нельзя отключать эти защиты, добавлять capabilities
или privileged ради запуска. Entrypoint завершится при неработающем sandbox.

Для проверенного Ubuntu 24.04 VPS подготовлен отдельный opt-in overlay:
[deploy/security/README.md](deploy/security/README.md). Он сохраняет non-root,
cap_drop ALL, no-new-privileges и Codex sandbox, добавляя необходимые правила
namespace/mount только в профилях Takopi. Следуйте инструкции установки
`compose.sandbox.yaml`, `security/`, `install-sandbox-profile.sh` и
`check-sandbox.py`. Общие sysctl, docker-default и другие сервисы не меняются.
Проверка на VPS 195.58.63.4 прошла, включая запрет записи вне проекта и IP sockets.

Добавьте отдельные рабочие repositories и настройте пути в config:

```bash
docker compose run --rm takopi git clone https://github.com/YOUR-ORG/jira-agent.git /workspace/jira-agent
docker compose run --rm takopi git clone https://github.com/YOUR-ORG/onas.git /workspace/onas
docker compose run --rm takopi codex login --device-auth
docker compose run --rm takopi codex login status
docker compose up -d
docker compose ps
docker compose logs --tail 100 takopi
```

Откройте показанный официальный URL на компьютере и введите device code.
При необходимости включите device login в account settings. Auth не извлекается
вручную. Уже реализованная login-модель сохранена. Login и подготовка projects
выполняются до первого запуска бота, чтобы он сразу получил готовый config.

Mounts: `data/codex` → `/home/codex/.codex` (auth/sessions), `data/takopi` →
`/home/codex/.takopi` (bridge state), `config/takopi.toml` → файл настроек внутри
этого каталога, read-only; `data/logs` → `/var/log/takopi`; `workspace` →
`/workspace`. Worktrees остаются внутри настроенного проекта. Создание mount
sources автоматически запрещено, чтобы опечатка не создала пустой каталог.
Не добавляйте host home/secrets, Docker socket или исходники bridge в mounts.

Container recreation/restart сохраняет auth, sessions, projects и worktrees.
Не удаляйте persistent directories. Для переноса старой установки сначала
сделайте backup старым Compose, остановите её и восстановите архив в новую
структуру по инструкции ниже; сохраните `.env` отдельно. Не запускайте две
копии бота одновременно с одним Telegram token.

## Update и rollback

Перед обновлением сохраните текущий tag, config и backup. Измените `IMAGE_TAG`
в `/srv/takopi/.env` на проверенный опубликованный tag, затем:

```bash
bash /srv/takopi/update-vps.sh
```

Скрипт выполняет `pull`, `up -d --no-build`, `ps`. Исходники bridge и Git для
этого не нужны. Он не делает backup автоматически: сделайте его до изменения
версии. Для другого runtime root можно задать `TAKOPI_RUNTIME_DIR`.

Rollback, например с `v0.2.1` на `v0.2.0`: измените только `IMAGE_TAG`, затем:

```bash
cd /srv/takopi
docker compose pull
docker compose up -d --no-build
docker compose ps
```

`latest` поддерживается для main, но production рекомендуется
закреплять на `v*` или `sha-<short-sha>`. Не перезаписывайте release tags.
При несовместимой миграции state восстановите backup совместимой версии;
возврат image сам по себе данные не откатывает. После update/rollback проверяйте
login status, health, session continuation и approval. GitHub Actions не
подключается к VPS и не выполняет автоматическое deployment.

## Backup и restore

```bash
TAKOPI_RUNTIME_DIR=/srv/takopi BACKUP_DIR=/srv/takopi-backups \
  bash /srv/takopi/backup.sh
```

Backup запрещает архив внутри runtime root (включая symlink/`..`), останавливает
бот, архивирует Codex auth/sessions, config/state, repositories/worktrees/logs,
затем запускает его через trap. Храните архив зашифрованно с правами 0600,
вне workspace/runtime root. `.env`, Compose и текущий image tag сохраняйте
отдельно в защищённом backup. Ничего не отправляется по сети.

Restore только из доверенного архива, при подготовленных directories и mounts:

```bash
cd /srv/takopi
docker compose stop takopi
# Config смонтирован read-only: восстановить его на host до запуска tar в container.
tar -xOzf /srv/takopi-backups/takopi-TIMESTAMP.tar.gz \
  home/codex/.takopi/takopi.toml > config/takopi.toml
chown 1001:1001 config/takopi.toml
chmod 600 config/takopi.toml
docker compose run --rm --no-deps -T takopi tar -xzf - -C / \
  --exclude=home/codex/.takopi/takopi.toml \
  < /srv/takopi-backups/takopi-TIMESTAMP.tar.gz
docker compose up -d --no-build
```

Container paths не изменились, поэтому архив прежнего named volume также
восстанавливается в bind directories. Перед переносом проведите restore drill.

## Runtime и проверка milestone

`/projects`, `/status`, `/abort`, `/diff`, `/tests`, `/commit` сохранены.
`/tests` использует только доверенный config `test_command` (`pytest`, `npm test`,
`npm run test`, `npm run lint`, `composer test`, `dotnet test`, `mvn test`,
`gradle test`) через Codex sandbox, без произвольного shell из Telegram.
`/commit` требует staged файлов и одноразового подтверждения с проверкой
index/HEAD/branch; push автоматически не выполняется.

Gateway показывает очищенную команду, request/turn/session, fingerprint и TTL
(по умолчанию 120s). Проверяются user/chat/thread/turn/request, replay и expiry.
Restart отзывает pending approvals. `/abort`, `/cancel`, steer и interrupt
продолжают работать. Hyphenated aliases допустимы в директивах, но не в bot menu.

Проверьте `/jira-agent @test/telegram-agent создай файл hello.txt с текстом hello`,
streaming, session ID, `/diff`, `/tests`, продолжение после `docker compose restart`,
approval для `git push`, DENY для `sudo apt install` и отказ чужим messages/callbacks.
Account-backed Telegram/Codex/VPS milestone ещё требует проверки оператором.

Health — heartbeat успешного polling и Codex binary; Docker не перезапускает
unhealthy автоматически. Следите за ним отдельно. Docker logs ротируются;
для `data/logs/application.jsonl` и audit настройте logrotate: `copytruncate`,
`daily`, `rotate 14`, `compress`, `missingok`, `notifempty`, `su 1001 1001`.

Non-root, cap_drop ALL, no-new-privileges, read-only rootfs, tmpfs/PID/CPU/memory
limits и весь Sprint 2 остаются. Общий UID bridge/Codex и broad read sandbox
не изолируют malicious repositories от same-UID auth/config. Не добавляйте
production credentials. Следующий архитектурный этап — отдельные workers;
этот change request его не реализует. SDK/dependencies меняются локально,
проверяются и собираются в CI, затем доставляются как новый image.

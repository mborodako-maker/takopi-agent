# Takopi на Ubuntu 24.04 VPS

Форк сохраняет штатные projects, Git worktrees, streaming, Codex app-server,
resume, steer и interrupt. Jira сейчас отсутствует. Не подключайте production
credentials. Сначала прочитайте SECURITY.md и ограничения gateway.

## Docker

Установите Docker Engine и Compose plugin из официального apt-репозитория:
<https://docs.docker.com/engine/install/ubuntu/>. Для чистой Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
printf 'Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: %s\nComponents: stable\nSigned-By: /etc/apt/keyrings/docker.asc\n' "$VERSION_CODENAME" | sudo tee /etc/apt/sources.list.d/docker.sources
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo docker compose version
```

Команды `docker` ниже выполняет администратор VPS. Членство в группе docker
эквивалентно root на хосте. Контейнер работает как codex, UID/GID 1001.
Не отключайте seccomp/AppArmor для исправления ошибки sandbox.

## Checkout и секреты

```bash
git clone <URL-ВАШЕГО-ФОРКА> takopi-vps
cd takopi-vps
sudo bash scripts/install.sh
sudo install -d -m 700 /srv/takopi-secrets
sudo install -m 600 .env.example /srv/takopi-secrets/takopi.env
sudoedit /srv/takopi-secrets/takopi.env
```

Создайте бота через официальный `@BotFather`, `/newbot`; токен положите только
в `/srv/takopi-secrets/takopi.env`. Узнайте свой **числовой** user ID через
`@userinfobot` (не отправляйте ему токен). Для private chat chat ID совпадает
с user ID; для группы используйте её числовой chat ID. Укажите:

```dotenv
TELEGRAM_BOT_TOKEN=<из BotFather>
TELEGRAM_ALLOWED_USER_IDS=123456789
TELEGRAM_CHAT_ID=123456789
CONTAINER_MEMORY_LIMIT=4G
CONTAINER_CPU_LIMIT=2.0
TZ=Europe/Minsk
```

Несколько user IDs разделяются запятыми. Пустой список и нечисловые значения
не запускаются. Username не участвует в проверке доступа. `.env.example` —
шаблон, не создавайте реальный `.env` в checkout или workspace.

Для последующих команд установите путь (сам файл не выполняется как shell):

```bash
export COMPOSE_ENV_FILES=/srv/takopi-secrets/takopi.env
```

При использовании sudo передавайте его явно:
`sudo env COMPOSE_ENV_FILES="$COMPOSE_ENV_FILES" docker compose ...`.
Не публикуйте вывод `docker compose config`/`docker inspect`: там есть env.

## Projects и Codex login

Отредактируйте `config/runtime/takopi.toml`; добавьте существующие Git repositories
в `workspace/`. Пример клонирования без передачи SSH-ключей контейнеру:

```bash
sudoedit config/runtime/takopi.toml
docker compose build
docker compose run --rm takopi /usr/local/bin/sandbox-check.sh
docker compose run --rm takopi git clone https://github.com/YOUR-ORG/jira-agent.git /workspace/jira-agent
docker compose run --rm takopi git clone https://github.com/YOUR-ORG/onas.git /workspace/onas
docker compose run --rm takopi codex login --device-auth
```

Если sandbox-check завершился с ошибкой, остановитесь до login и запуска бота;
см. раздел совместимости в конце инструкции. Этот шаг не требует credentials
Codex и проверяет реальный container security profile.

Откройте показанный официальный URL на своём компьютере и введите одноразовый
код. Включите device code login в security settings ChatGPT, если требуется.
Flow описан в [официальной документации OpenAI](https://developers.openai.com/codex/auth).
Вручную извлекать токены из auth.json не нужно. Auth и sessions хранятся в
named volume `codex-storage`, вне workspace. Проверка:

```bash
docker compose run --rm takopi codex login status
docker compose up -d --build
docker compose logs -f
docker compose ps
```

Для браузерного fallback можно временно запустить `codex login` с loopback-only
port forwarding 1455 через SSH своего администратора; не открывайте порт всем.
Это административный login, бот не получает SSH-доступа. Предпочтителен device flow.

Dockerfile устанавливает официальный npm-пакет `@openai/codex` фиксированной
версии. Версию меняйте после проверки app-server протокола и security tests.
Runtime содержит Python, Node, Git, ripgrep. PHP/.NET/JDK и зависимости проектов
добавляйте при сборке собственного образа; установка пакетов в read-only rootfs
runtime намеренно ограничена. Resource limits задаются env без изменения образа.

## Проверка milestone

1. Напишите боту `/jira-agent @test/telegram-agent создай файл hello.txt с текстом hello`.
2. Проверьте streaming progress и session ID. Worktree создаёт штатный Takopi.
3. Запросите `/diff` и `/tests` (настройка тестов описана ниже).
4. Выполните `docker compose restart`; продолжите диалог в том же чате.
5. Проверьте approval на `git push`, отказ для `sudo apt install`, отказ
   сообщениям и callback от второго пользователя вне allowlist.

Не отправляйте боту auth.json, .env или приватные ключи. Удаление контейнера и
`docker compose restart` сохраняют volumes; `docker compose down -v` уничтожает
Codex storage, поэтому эту команду не используйте.

## Health, logs, restart

```bash
docker compose logs --tail 100 takopi
docker compose restart takopi
```

Health проверяет доступность Codex binary и свежий heartbeat успешного polling
(не просто PID). Интервал 30s, timeout 10s, 3 попытки. Docker restart policy
перезапускает завершившийся процесс; статус unhealthy сам по себе контейнер
не перезапускает. Настройте мониторинг unhealthy и возраста heartbeat.

Structured logs: `logs/application.jsonl`, stdout Docker. Docker logs ротируются.
Для application/audit используйте logrotate с `copytruncate`, `daily`, `rotate 14`,
`compress`, `missingok`, `notifempty`, `su 1001 1001`; директория должна быть
недоступна другим пользователям. Даже очищенные логи могут содержать project metadata.

## Backup, restore, update

```bash
export BACKUP_DIR=/srv/takopi-backups
bash scripts/backup.sh
bash scripts/update.sh
```

Backup останавливает бот, архивирует config/state, Codex auth/sessions, repositories,
worktrees и logs, затем запускает бот через trap. Архив содержит credentials:
храните зашифрованно, права 0600, вне workspace и checkout. Секретный env сохраняйте
отдельно в защищённом backup. Скрипт не отправляет архив по сети.

Restore на совместимом образе, в том же compose project (с тем же именем volume):

```bash
docker compose stop takopi
docker compose run --rm --no-deps -T takopi tar -xzf - -C / < /srv/takopi-backups/takopi-TIMESTAMP.tar.gz
docker compose up -d
```

Восстанавливайте только доверенный собственный архив. Сохранены абсолютные
container paths `/workspace/...`; они не зависят от расположения checkout хоста.
Update делает backup, `git pull --ff-only`, build и restart. При конфликте upstream
сначала изучите `docs/UPSTREAM_CHANGES.md`. Проверяйте login status и smoke test
после каждого обновления.

## VPS commands и approvals

`/projects` перечисляет проекты. `/status` показывает выбранный worktree,
session, активный turn и elapsed. `/abort` вызывает штатный interrupt для turn
выбранного чата/topic/worktree. `/cancel` и upstream кнопки продолжают работать.
`/diff` показывает stat, tracked diff и содержимое небольших новых файлов;
большой diff отправляется как `changes.diff`, с тем же лимитом размера и
обязательным исключением секретных путей. Изменения файлов, содержащих секреты
внутри обычного исходного текста, требуют отдельной дисциплины оператора.

В таблице проекта укажите `test_command = "pytest"` либо один из явно разрешённых
вариантов (`npm test`, `npm run test`, `npm run lint`, `composer test`, `dotnet test`,
`mvn test`, `gradle test`). `/tests` запускает точный argv через официальный
`codex sandbox`, с timeout/output limits; пользовательский shell не принимается.
Строки вроде `/tests rm -rf /` отвергаются. Если sandbox недоступен, тесты
возвращают ошибку, без unsandboxed fallback.

`/commit` работает только с уже staged файлами. Показывает branch/files/message,
запрашивает одноразовое подтверждение в Telegram и проверяет, что index/HEAD/branch
не изменились за время ожидания. Не выполняет `git add .` и не делает push.
Git hooks/fsmonitor/GPG signing в этом локальном workflow отключены.
Не запускайте параллельно внешние операции с index во время commit approval.

После выбора `/jira-agent @branch ...` VPS команды помнят последний контекст
конкретного пользователя и topic; state хранится рядом с Takopi config. Можно
также ответить на progress message или явно указать директиву проекта/ветки.
Это не заменяет штатные Codex sessions и Git worktrees. Hyphenated aliases
поддерживаются текстовыми директивами, но Telegram bot menu допускает только
буквы, цифры и underscore, поэтому такой alias в меню не появится.

Gateway показывает request/turn/session, команду с очищенными credential-shaped
аргументами, TTL и fingerprint. Убедитесь, что действие понятно, перед
«Разрешить». Default TTL 120s, можно уменьшить `approval_ttl_s` в
`[transports.telegram]`. Истёкшая, использованная или чужая кнопка не сработает;
restart уничтожает все незавершённые approvals. Отказ и timeout блокируют операцию.
Для sandbox escalation даже read/test команда потребует отдельного approval:
такое разрешение шире обычного запуска внутри sandbox.

**Проверка совместимости до login/start:**

```bash
docker compose run --rm takopi /usr/local/bin/sandbox-check.sh
```

Текущая среда разработки отклонила создание Bubblewrap namespace при стандартном
Docker security profile; Landlock compatibility flag также не решил проблему.
Entrypoint теперь отказывается запускать бот при этой ошибке. На целевом VPS
проверьте поддержку rootless user namespaces и совместимость Codex с локальными
seccomp/AppArmor policies. Не отключайте sandbox/seccomp/AppArmor и не добавляйте
capabilities ради запуска. До успешной проверки реальный VPS milestone не пройден.

Если сборка возвращает `EROFS` / `Input/output error` или Docker daemon недоступен,
сначала восстановите filesystem/Docker на хосте. Это отдельная проблема среды,
не повод изменять security profile контейнера. Повторите build и preflight после
восстановления; текущий повторный аудит встретил именно такой сбой среды.

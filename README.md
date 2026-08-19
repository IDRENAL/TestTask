# Script Manager

Веб-сервис для обнаружения, запуска и планирования Python-скриптов. Проект разворачивается
через Docker Compose и состоит ровно из трёх контейнеров:

- `frontend` — Nginx со статическим HTML/CSS/JavaScript;
- `backend` — FastAPI, APScheduler и запуск Python-скриптов;
- `database` — PostgreSQL для расписаний, состояния и истории выполнений.

PostgreSQL отдельно устанавливать и создавать не нужно: Compose скачивает образ, создаёт БД,
пользователя и постоянный Docker volume автоматически.

## Возможности

- автоматическое обнаружение `.py`-файлов из каталога `scripts/`;
- просмотр списка скриптов сразу после открытия frontend;
- изменение пятикомпонентного cron-расписания;
- ручной запуск скрипта через `Run now`;
- пауза и возобновление расписания;
- защита изменяющих API-операций ключом `X-API-Key`;
- запрет одновременного выполнения двух экземпляров одного скрипта;
- сохранение stdout, stderr, exit code, типа запуска и времени выполнения в PostgreSQL;
- восстановление расписаний и состояния pause/resume после перезапуска backend.

Отображение execution logs во frontend не реализовано. Логи сохраняются в БД и доступны
через PostgreSQL.

## Архитектура

```text
Browser
  │
  ├── :8080 ──> frontend (Nginx) ──> /api/* ──> backend:8000
  │
  └── :8000 ─────────────────────────────────> backend (FastAPI + APScheduler)
                                                        │
                                                        ├── PostgreSQL
                                                        └── /app/scripts (read-only mount)
```

По умолчанию frontend доступен на порту `8080`, backend — на `8000`. PostgreSQL опубликован
только на loopback-интерфейсе хоста: `127.0.0.1:15432` → `database:5432`. Из интернета этот
порт недоступен.

Backend запускается с одним worker. Увеличивать число backend workers без изменения
архитектуры scheduler нельзя: каждый процесс создаст собственный APScheduler.

## Требования

Для обычного запуска нужны только:

- Docker Engine;
- Docker Compose plugin с командой `docker compose`;
- доступ сервера к `docker.io`, `pypi.org` и `files.pythonhosted.org` для загрузки образов и
  Python-пакетов.

Python, PostgreSQL, Nginx и `uv` на хост устанавливать не требуется.
Для приведённых ниже SSH-инструкций на локальной машине также нужны `ssh` и `rsync`, а
`openssl` используется только для генерации случайных значений `.env`.

Проверка окружения:

```bash
docker --version
docker compose version
```

## Конфигурация

Compose автоматически читает файл `.env` из корня проекта.

| Переменная | Обязательна | Значение по умолчанию | Назначение |
| --- | --- | --- | --- |
| `SCRIPT_MANAGER_API_KEY` | да | — | Ключ длиной не менее 16 символов для mutating API. |
| `POSTGRES_PASSWORD` | да | — | Пароль PostgreSQL. Должен быть URL-safe, так как включается в DSN. |
| `FRONTEND_PORT` | нет | `8080` | Порт frontend на хосте. |
| `BACKEND_PORT` | нет | `8000` | Порт backend на хосте. |
| `POSTGRES_PORT` | нет | `15432` | Loopback-порт PostgreSQL на хосте. |
| `SCHEDULER_TIMEZONE` | нет | `UTC` | Часовой пояс cron, например `Europe/Moscow`. |

Файл `.env` исключён из Git. [`.env.example`](.env.example) содержит только placeholders и не
должен использоваться с неизменёнными значениями в публичном окружении.

### Безопасное создание `.env`

Следующая команда создаёт новые hex-ключи и перезаписывает `.env`. Выполняйте её только при
первичной настройке либо когда существующий `.env` больше не нужен:

```bash
umask 077
{
  printf 'SCRIPT_MANAGER_API_KEY='
  openssl rand -hex 32
  printf 'POSTGRES_PASSWORD='
  openssl rand -hex 32
  printf 'FRONTEND_PORT=8080\n'
  printf 'BACKEND_PORT=8000\n'
  printf 'POSTGRES_PORT=15432\n'
  printf 'SCHEDULER_TIMEZONE=UTC\n'
} > .env
chmod 600 .env
```

Сгенерированный `SCRIPT_MANAGER_API_KEY` понадобится для управляющих действий во frontend.


## Локальный запуск

Из корня проекта:

```bash
docker compose config --quiet
docker compose up --build --detach --wait
docker compose ps
```

Ожидаемое состояние — три контейнера со статусом `healthy`:

```text
database
backend
frontend
```

После запуска доступны:

- frontend: <http://localhost:8080>;
- backend healthcheck: <http://localhost:8000/health>;
- OpenAPI/Swagger UI: <http://localhost:8000/docs>.

Frontend публично получает список скриптов. Для изменения cron, ручного запуска и
pause/resume введите `SCRIPT_MANAGER_API_KEY` в поле `Control access`. Ключ хранится только в
памяти открытой страницы и добавляется в заголовок `X-API-Key`.

Просмотр логов контейнеров:

```bash
docker compose logs --tail=100 backend
docker compose logs --tail=100 frontend
docker compose logs --tail=100 database
```

Остановка без удаления данных:

```bash
docker compose stop
```

Удаление контейнеров и network с сохранением PostgreSQL volume:

```bash
docker compose down
```

Команда `docker compose down --volumes` удаляет БД и всю историю выполнений. Не используйте её,
если данные нужно сохранить.

## Развёртывание на другом сервере по SSH

Ниже предполагается, что Docker уже установлен на удалённом сервере, а текущая директория на
локальной машине — корень этого проекта.

### 1. Скопировать проект

```bash
rsync -az \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='.env' \
  --exclude='.idea' \
  --exclude='.codex' \
  --exclude='__pycache__' \
  --exclude='.pytest_cache' \
  --exclude='.ruff_cache' \
  --exclude='.mypy_cache' \
  --exclude='.coverage' \
  ./ deploy@SERVER_IP:~/script-manager/
```



### 2. Подключиться и создать конфигурацию

```bash
ssh deploy@SERVER_IP
cd ~/script-manager
umask 077
{
  printf 'SCRIPT_MANAGER_API_KEY='
  openssl rand -hex 32
  printf 'POSTGRES_PASSWORD='
  openssl rand -hex 32
  printf 'FRONTEND_PORT=8080\n'
  printf 'BACKEND_PORT=8000\n'
  printf 'POSTGRES_PORT=15432\n'
  printf 'SCHEDULER_TIMEZONE=Europe/Moscow\n'
} > .env
chmod 600 .env
```

Сохраните API key в выбранном password manager: он потребуется оператору frontend.

### 3. Собрать и поднять сервис

```bash
docker compose config --quiet
docker compose up --build --detach --wait
docker compose ps
```

### 4. Настроить сетевой доступ


- TCP `8080` — frontend;
- TCP `8000` — backend.

Их нужно разрешить одновременно в firewall сервера и в security group/VPS firewall облачного
провайдера. Пример для UFW:

```bash
sudo ufw allow 8080/tcp
sudo ufw allow 8000/tcp
```

Порты PostgreSQL `5432` и `15432` в firewall открывать не нужно: host-порт `15432` привязан
только к `127.0.0.1`.

Проверка с локального компьютера:

```bash
curl http://SERVER_IP:8000/health
curl http://SERVER_IP:8080/api/scripts
```

Интерфейс будет доступен по адресу `http://SERVER_IP:8080`, документация backend — по
`http://SERVER_IP:8000/docs`.

Проект сам по себе не настраивает DNS, TLS или внешний firewall. Перед использованием в
недоверенной публичной сети рекомендуется поставить host-level reverse proxy с HTTPS и
ограничить прямой доступ к backend там, где это допускают требования окружения.

### Обновление удалённого сервера

Повторите `rsync` с локальной машины — `.env` не будет перезаписан — и выполните на сервере:

```bash
cd ~/script-manager
docker compose up --build --detach --wait
```

Docker volume PostgreSQL при таком обновлении сохраняется.

## Встроенные скрипты

| Файл | Начальное расписание | Назначение |
| --- | --- | --- |
| `resource_monitor.py` | `* * * * *` | Каждую минуту проверяет `example.com`, `python.org` и `github.com`; выводит JSON с URL, временем и HTTP status. |
| `disk_usage.py` | `*/5 * * * *` | Записывает размер, занятое и свободное место файловой системы. |
| `runtime_info.py` | `0 * * * *` | Записывает версию Python, платформу, число CPU и load average. |

`resource_monitor.py` выполняет три проверки параллельно. Ошибка DNS, timeout или HTTP error
также записывается как результат; в случае отсутствия HTTP-ответа поле `http_status` равно
`null`.

Начальные расписания применяются только при первой регистрации скрипта. Последующие изменения
хранятся в PostgreSQL и не перезаписываются при рестарте.

### Добавление собственного скрипта

1. Поместите `.py`-файл непосредственно в каталог `scripts/`.
2. Перезапустите backend:

   ```bash
   docker compose restart backend
   ```

3. Новый скрипт появится во frontend с расписанием `0 * * * *` и состоянием `paused`.

Каталог монтируется в backend read-only. Скрипты запускаются напрямую текущим Python
интерпретатором без shell и получают минимальный набор environment variables. Добавляйте только
доверенный код: каждый файл в этом каталоге потенциально может быть запущен оператором.

## Поведение scheduler

- Используется стандартное cron-выражение из пяти полей: `minute hour day month day_of_week`.
- Часовой пояс задаётся через `SCHEDULER_TIMEZONE`.
- `Run now` работает независимо от состояния paused/scheduled.
- `Pause` запрещает будущие запуски, но не завершает уже работающий процесс.
- Для одного скрипта одновременно выполняется не более одного экземпляра.
- Повторный ручной запуск работающего скрипта возвращает HTTP `409`.
- Максимальное время одного выполнения — 60 секунд.
- Незавершённая при остановке backend запись помечается как `interrupted` после следующего старта.

## API

Публичные endpoints:

```text
GET /health
GET /api/scripts
GET /docs
```

Endpoints, требующие заголовок `X-API-Key`:

```text
PATCH /api/scripts/{id}/schedule
POST  /api/scripts/{id}/run
POST  /api/scripts/{id}/pause
POST  /api/scripts/{id}/resume
```

Пример изменения расписания:

```bash
CONTROL_KEY='value-from-your-env-file'
curl \
  --header "X-API-Key: ${CONTROL_KEY}" \
  --header 'Content-Type: application/json' \
  --request PATCH \
  --data '{"cron_expression":"*/10 * * * *"}' \
  http://localhost:8000/api/scripts/1/schedule
```

Основные ошибки API:

- `401` — отсутствует или неверен API key;
- `404` — скрипт не найден или больше недоступен;
- `409` — скрипт уже выполняется;
- `422` — невалидное cron-выражение или тело запроса.

## Данные и execution logs

При первом старте backend автоматически создаёт таблицы:

- `scripts` — имя файла, cron, enabled/paused и доступность файла;
- `executions` — trigger type, status, timestamps, return code, stdout и stderr.

Данные хранятся в named volume `database-data`. Пример read-only просмотра последних запусков:

```bash
docker compose exec database \
  psql -U script_manager -d script_manager \
  -c 'SELECT id, script_id, trigger_type, status, started_at, finished_at, return_code FROM executions ORDER BY id DESC LIMIT 20;'
```

Для подключения с хоста через `psql`, DBeaver или DataGrip используйте:

```text
Host:     127.0.0.1
Port:     15432 (либо POSTGRES_PORT из .env)
Database: script_manager
User:     script_manager
Password: POSTGRES_PASSWORD из .env
```

Для доступа к БД на удалённом сервере не открывайте PostgreSQL в интернет. Создайте SSH-туннель
с локального компьютера:

```bash
ssh -N -L 15432:127.0.0.1:15432 deploy@SERVER_IP
```

После этого подключайтесь к `127.0.0.1:15432`. Если локальный порт занят, замените первое число,
например: `-L 15433:127.0.0.1:15432`.

Важно: изменение `POSTGRES_PASSWORD` в `.env` не меняет пароль роли внутри уже
инициализированного PostgreSQL volume. Для существующей БД пароль нужно менять согласованно с
ролью PostgreSQL либо предварительно делать backup и создавать новый volume.

## Разработка и проверки

Для запуска Python-проверок вне Docker нужны Python `>=3.12` и `uv`:

```bash
uv sync --dev
uv run pytest
uv run pytest --cov=script_manager --cov-report=term-missing
uv run ruff check .
uv run ruff format --check .
uv run mypy
docker compose config --quiet
```

Тесты проверяют публичный список, авторизацию mutations, cron validation, pause/resume,
персистентность состояния, запрет overlapping runs, запись execution output и работу встроенных
скриптов.

## Диагностика

### Compose сообщает, что переменная не задана

Проверьте наличие `.env` и обязательных переменных:

```bash
docker compose config --quiet
```

Не выводите содержимое `.env` в общие логи или issue tracker.

### Контейнер не стал healthy

```bash
docker compose ps
docker compose logs --tail=200 database
docker compose logs --tail=200 backend
docker compose logs --tail=200 frontend
```

### Не загружаются Docker images

Для первой сборки серверу нужен HTTPS-доступ к Docker Hub и PyPI. Ошибки получения Docker
metadata, DNS failures и network timeout обычно означают проблему исходящего соединения или
временную недоступность registry/package index.

### Скрипт не появился во frontend

Проверьте, что файл:

- имеет расширение `.py`;
- лежит непосредственно в `scripts/`;
- не начинается с `_`.

После добавления файла перезапустите backend.

# Демо-сервер ProЗапас

Адрес API: https://185.196.117.2/api/v1

Swagger: https://185.196.117.2/api/v1/docs

## Автоматический деплой

GitHub Actions `CI and deployment` проверяет Pull Request в `main` и коммиты
в `main`. Все три задания (`Backend tests`, `Desktop tests`, `System tests`)
обязательны; исходники запускаются с отдельным PostgreSQL и через настоящий Qt UI.
Рабочие ветки создают от `main`; постоянной ветки `dev` больше нет.

PR принимает только `@dkarasevgthub`. `.github/CODEOWNERS` требует его review для
изменений других авторов. Владелец может принять собственный PR вручную: GitHub
не разрешает авторам одобрять свои PR. Обязательные тесты остаются обязательными
для всех. Rulesets запрещают удаление и force-push `main` и `deploy`.

После принятия PR workflow проверяет свежий `main`, его преемственность с `deploy`
и вызывает ограниченную SSH-команду `deploy <SHA>`. Исходники каждого коммита
лежат в `/opt/prozapas/releases/<SHA>`; `/opt/prozapas/current` указывает на успешно
запущенную версию. База, секреты и сертификаты сохраняются; перед миграциями
создаётся дамп в `/opt/prozapas/deployment/backups`. Сид при обновлении не запускается.
При ошибке запуска возвращаются прежние API и прокси; миграции базы автоматически
не откатываются, для восстановления сохраняется дамп.

Только после успешной проверки публичного API и его точного SHA сервер обновляет
ветку `deploy` через `/root/.ssh/prozapas_release_marker`. Это отдельный write deploy
key репозитория: он может менять `deploy`, но не `main`. При ошибке публикации
сервер продолжает работать; повторный запуск workflow для `main` допубликует
отметку без повторных миграций и дампа. Расходящаяся история не перезаписывается.

Статус обновления виден во вкладке **Actions**. PR и пуши в рабочие ветки не
обновляют сервер. Отдельные правки в `deploy` не вносят. `deploy` хранит тот же
исходный код, что принятый `main`, а его вершина фиксирует установленную версию.

SSH-секреты `PROZAPAS_DEPLOY_KEY` и `PROZAPAS_KNOWN_HOSTS` находятся в GitHub Environment
`production`, доступном только ветке `main`. У Actions нет права записи в Git.
SSH-ключ разрешает только `deploy <полный SHA>` текущего `main`, без оболочки.

Первичная серверная настройка от root: `python3 deployment/install_autodeploy.py`.
Публичный `/root/.ssh/prozapas_release_marker.pub` регистрируют как write deploy key
репозитория; host keys `github.com` закрепляют из официального API метаданных GitHub.
После изменения `deploy.py`/`deploy_ssh.py` повторяют установку доверенных скриптов.
Правила доступа сохранены в `deployment/github-rulesets.json`.

Команды обслуживания после первого автодеплоя:

```sh
cd /opt/prozapas/current/deployment
docker compose ps
docker compose logs --tail 100 api proxy
cat /opt/prozapas/deployment/deployed.json
```

Установка находится в `/opt/prozapas`, команды выполняются из
`/opt/prozapas/deployment`. Сервисы запускаются отдельным проектом Docker Compose:
PostgreSQL, API и Caddy. Порты 80 и 443 опубликованы, база и API доступны только
внутри сети проекта. Контейнеры автоматически стартуют после перезапуска сервера,
Caddy получает и продлевает сертификат HTTPS на IP через профиль Let's Encrypt
`shortlived`. Данные хранятся в Docker volumes.

```sh
docker compose ps
docker compose logs --tail 100 api proxy
docker compose up -d --wait api proxy
```

Первичная установка:

```sh
python3 setup_env.py
docker compose build api migrate seed
docker compose up -d --wait db
docker compose run --rm migrate upgrade head
docker compose run --rm seed
docker compose up -d --wait api proxy
python3 write_access.py
```

`seed` загружает вымышленные склады и товары, а пользователей берёт из приватного
`demo-users.json`. Здесь настроены Карасев Дмитрий (`karasev`, склад 128), Литвинцев
Матвей (`litvin`, склад 129) и Козлов Дмитрий (`kozlov`, склад 130). Все трое имеют
права администратора для демонстрации. Старые сид-пользователи удалены мягко:
они не могут войти и не видны в справочнике, но их подписи в истории сохраняются.
Пароли записаны в `access.txt`; локальная копия лежит в этом же каталоге проекта.
`demo-users.json`, `access.txt` и серверный `.env` не коммитятся.

Клиент читает `desktop/.env`; в текущем рабочем месте он настроен на сервер.
На другом компьютере создайте `.env` рядом с программой:

```dotenv
PROZAPAS_API=https://185.196.117.2/api/v1
PROZAPAS_TLS_VERIFY=true
```

Проверка после обновления:

```sh
docker compose run --rm migrate check
curl --fail https://185.196.117.2/api/v1/health
```

Перед обновлением сохраните резервную копию:

```sh
umask 077
mkdir -p backups
docker compose exec -T db pg_dump -U prozapas_user -d prozapas_db -Fc > "backups/prozapas-$(date +%Y%m%d-%H%M%S).dump"
```

Для остановки с сохранением данных: `docker compose down`. Не добавляйте `-v`,
если данные нужны: этот флаг удаляет тома.

API-тесты на сервере: `python3 run_tests.py`. Скрипт пересоздаёт только отдельную
базу `prozapas_test` в PostgreSQL этого проекта; демонстрационная база не меняется.

Сетевая проверка с компьютера разработчика:

```powershell
desktop/.venv/Scripts/python.exe deployment/check_desktop.py
```

Она использует HTTP-клиент приложения, проверяет сертификат, вход и справочники,
затем создаёт и завершает демонстрационный заказ на 0,5 м трубы. Его история
остаётся в базе, остатки между складами изменяются на 0,5 м.

# Демо-сервер ProЗапас

Адрес API: https://185.196.117.2/api/v1

Swagger: https://185.196.117.2/api/v1/docs

## Автоматический деплой

GitHub Actions `CI and deployment` запускает проверки на пуши в `dev`, `main`
и `deploy`, а также на Pull Request в `main` и `dev`. Скрипты деплоя и API-тесты
проверяются на отдельном PostgreSQL в GitHub; тесты десктопа выполняются на Windows.
После успешных проверок пуша в `main` этот же коммит переносится в `deploy`
обычным fast-forward и выкладывается на сервер по ограниченному SSH-ключу.
Пуши в `dev` и проверки Pull Request сервер не обновляют.
История `deploy` не переписывается: при расхождении с `main` автоматический
перенос останавливается до объединения изменений в `main`.

На сервере исходники каждого коммита лежат в `/opt/prozapas/releases/<SHA>`;
`/opt/prozapas/current` указывает на успешно запущенную версию. База данных,
серверные секреты и сертификаты сохраняются. Перед миграциями создаётся дамп
в `/opt/prozapas/deployment/backups`. Сид при обновлении не запускается.
При ошибке запуска скрипт возвращает прежние API и прокси; миграции базы
автоматически не откатываются, для восстановления сохраняется дамп.

Статус и журнал обновления: вкладка **Actions** в репозитории. Для выпуска
объедините Pull Request `dev` → `main` после успешных проверок. В `deploy`
отдельные правки не вносят. Workflow можно повторить во вкладке Actions
для `main` или `deploy`; запуск для `dev` выполняет только проверки.
Перенос и деплой выполняются в одном workflow: пуш от `GITHUB_TOKEN`
не запускает второй workflow автоматически.

Секреты GitHub Actions: `PROZAPAS_DEPLOY_KEY` и `PROZAPAS_KNOWN_HOSTS`.
Ключ не даёт интерактивную оболочку: разрешена только команда
`deploy <полный SHA>`, и только для текущей вершины ветки `deploy`.
Первичная серверная настройка выполняется от root:
`python3 deployment/install_autodeploy.py`. После изменений `deploy.py`
или `deploy_ssh.py` нужно повторить установку скриптов на сервере.

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

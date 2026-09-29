up: .env                  ## поднять стенд целиком: база, миграции, сид, API
	docker compose up -d db
	docker compose up -d api
	docker compose run --rm migrate upgrade head
	docker compose run --rm seed

# Цель без зависимостей: отрабатывает один раз, пока .env не существует, и
# никогда не перепишет уже настроенный. JWT_SECRET генерируется — его никто
# не набирает руками, а заглушка из примера ушла бы в первый же запуск.
# Пароли остаются из примера: SEED_PASSWORD набирают при входе.
.env:                     ## .env для стенда из .env.example
	uv run python -c "import pathlib,re,secrets; t=pathlib.Path('.env.example').read_text(encoding='utf-8'); t=re.sub(r'(?m)^JWT_SECRET=.*$$', 'JWT_SECRET='+secrets.token_urlsafe(48), t); pathlib.Path('.env').write_text(t, encoding='utf-8')"
	@echo "создан .env: пароли из .env.example, JWT_SECRET свой"

down:                     ## остановить всё, данные сохранить
	docker compose down

migrate:                  ## накатить до последней ревизии
	docker compose run --rm migrate upgrade head

rollback:                 ## шаг назад
	docker compose run --rm migrate downgrade -1

forward:                  ## шаг вперёд
	docker compose run --rm migrate upgrade +1

current:                  ## где мы сейчас
	docker compose run --rm migrate current --verbose

history:                  ## все ревизии
	docker compose run --rm migrate history --verbose

check:                    ## схема разошлась с моделями?
	docker compose run --rm migrate check

test:                     ## тесты API: нужна поднятая база (make up)
	uv run pytest

seed:                     ## наполнить базу для тестовых прогонов
	docker compose run --rm seed

reset:                    ## снести базу целиком и собрать заново
	docker compose down -v
	docker compose up -d db
	docker compose run --rm migrate upgrade head
	docker compose run --rm seed

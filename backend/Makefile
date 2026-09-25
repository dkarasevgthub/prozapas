up:                       ## поднять базу и накатить миграции
	docker compose up -d db
	docker compose up -d api
	docker compose run --rm migrate upgrade head

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

seed:                     ## наполнить базу для тестовых прогонов
	docker compose run --rm seed

reset:                    ## снести базу целиком и собрать заново
	docker compose down -v
	docker compose up -d db
	docker compose run --rm migrate upgrade head
	docker compose run --rm seed

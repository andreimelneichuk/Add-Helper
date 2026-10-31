.PHONY: setup check check-web prototype backup restore-check down

# Поднимает окружение на чистом клоне; повторный запуск безопасен.
setup: .env
	uv sync --locked
	docker compose up --detach --wait --build

.env:
	sed "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$$(openssl rand -hex 24)/" .env.example > .env
	chmod 600 .env

# Те же проверки запускает CI. Нужна запущенная база: make setup.
check:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy
	uv run lint-imports
	uv run pytest
	$(MAKE) check-web

check-web:
	npm --prefix frontend ci --no-audit --no-fund
	npm --prefix frontend run check

# Прототип на тестовых данных: http://localhost:5173
prototype:
	npm --prefix frontend ci --no-audit --no-fund
	npm --prefix frontend run dev

# Резервная копия в backups/ и проверка восстановления во временную базу.
backup:
	scripts/backup.sh

restore-check:
	scripts/restore-check.sh

down:
	docker compose down

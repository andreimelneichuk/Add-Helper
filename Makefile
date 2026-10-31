.PHONY: setup check down

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

down:
	docker compose down

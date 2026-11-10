# Этап 1: сборка клиентского приложения
FROM node:22-alpine AS frontend-builder
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# Этап 2: образ бэкенда с Python и браузером
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.6 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --locked --no-dev
# Проверяем загрузку приложения очереди прямо в образе: worker ссылается на тот же модуль,
# который запускает scripts/start.sh. Ошибку импорта ловим при сборке, до выкладки.
RUN python -c "from procrastinate.cli import load_app; load_app('adhelper.worker.app.app')"

# Браузер для сайтов, которые рисует скрипт (правила site-2). Только headless-оболочка
# с системными библиотеками; путь общий, чтобы её видел непривилегированный пользователь.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN playwright install --with-deps --only-shell chromium && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home adhelper
# Копируем собранный фронтенд в директорию frontend/dist, откуда FastAPI отдаёт SPA и статику
COPY --from=frontend-builder --chown=adhelper:adhelper /web/dist /app/frontend/dist

# Скрипт одновременного запуска воркера и веб-сервера (для Render / единого контейнера)
COPY --chown=adhelper:adhelper scripts/start.sh /app/scripts/start.sh
RUN chmod +x /app/scripts/start.sh

USER adhelper

EXPOSE 8000
CMD ["/app/scripts/start.sh"]

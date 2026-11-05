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

# Браузер для сайтов, которые рисует скрипт (правила site-2). Только headless-оболочка
# с системными библиотеками; путь общий, чтобы её видел непривилегированный пользователь.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN playwright install --with-deps --only-shell chromium && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home adhelper
USER adhelper

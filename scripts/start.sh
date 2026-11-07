#!/bin/bash
set -e

# Применяем миграции базы данных перед стартом
alembic upgrade head

POLL_INTERVAL="${PROCRASTINATE_POLL_INTERVAL:-15.0}"

# Запускаем фоновые воркеры: страж бюджета в отдельном процессе, чтобы долгие генерации его не задерживали
procrastinate --app=adhelper.worker.app.app worker --fetch-job-polling-interval="$POLL_INTERVAL" --queues=budget &
BUDGET_WORKER_PID=$!

procrastinate --app=adhelper.worker.app.app worker --fetch-job-polling-interval="$POLL_INTERVAL" --concurrency=2 --queues=creatives,notifications &
WORKER_PID=$!

# Запускаем веб-сервер uvicorn
uvicorn --factory adhelper.api.app:create_app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16}" --no-server-header &
UVICORN_PID=$!

# Корректная остановка обоих процессов при остановке или перезапуске контейнера
cleanup() {
    echo "Shutting down workers and web server..."
    kill -TERM "$BUDGET_WORKER_PID" 2>/dev/null || true
    kill -TERM "$WORKER_PID" 2>/dev/null || true
    kill -TERM "$UVICORN_PID" 2>/dev/null || true
    wait "$BUDGET_WORKER_PID" 2>/dev/null || true
    wait "$WORKER_PID" 2>/dev/null || true
    wait "$UVICORN_PID" 2>/dev/null || true
}
trap cleanup SIGTERM SIGINT

# Ожидаем завершения любого из процессов: если один падает, контейнер перезапускается
wait -n "$UVICORN_PID" "$BUDGET_WORKER_PID" "$WORKER_PID"
EXIT_CODE=$?
cleanup
exit $EXIT_CODE

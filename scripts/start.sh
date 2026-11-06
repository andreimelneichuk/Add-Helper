#!/bin/bash
set -e

# Применяем миграции базы данных перед стартом
alembic upgrade head

# Запускаем фоновый воркер (разборы сайтов, генерация объявлений, расписания)
procrastinate --app=adhelper.worker.app.app worker --queues=budget,creatives,notifications &
WORKER_PID=$!

# Запускаем веб-сервер uvicorn
uvicorn --factory adhelper.api.app:create_app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips=* &
UVICORN_PID=$!

# Корректная остановка обоих процессов при остановке или перезапуске контейнера
cleanup() {
    echo "Shutting down worker (PID $WORKER_PID) and web server (PID $UVICORN_PID)..."
    kill -TERM "$WORKER_PID" 2>/dev/null || true
    kill -TERM "$UVICORN_PID" 2>/dev/null || true
    wait "$WORKER_PID" 2>/dev/null || true
    wait "$UVICORN_PID" 2>/dev/null || true
}
trap cleanup SIGTERM SIGINT

# Ожидаем завершения любого из процессов: если один падает, контейнер перезапускается
wait -n "$UVICORN_PID" "$WORKER_PID"
EXIT_CODE=$?
cleanup
exit $EXIT_CODE

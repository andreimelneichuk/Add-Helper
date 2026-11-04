# Веб-сервер: собранный интерфейс и прокси к API. Контекст сборки — корень репозитория.
FROM node:22-alpine AS build
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/index.html frontend/tsconfig.json frontend/vite.config.ts ./
COPY frontend/src ./src
RUN npm run build

FROM caddy:2.8-alpine
COPY --from=build /web/dist /srv
COPY deploy/Caddyfile /etc/caddy/Caddyfile

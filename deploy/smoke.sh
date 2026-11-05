#!/bin/sh
# Проверка после установки: интерфейс, API, заголовки и закрытые служебные пути.
# Запуск: deploy/smoke.sh https://ваш-домен   (для DOMAIN=localhost добавьте --insecure)
set -eu
BASE="${1:?Укажите адрес, например https://ads.example.ru}"
CURL="curl --silent --show-error --max-time 10 ${2:-}"
fail() { echo "НЕ ПРОШЛО: $1"; exit 1; }

$CURL --fail "$BASE/" | grep -q '<div id="root">' || fail "интерфейс не отдаётся"
$CURL --fail "$BASE/healthz" | grep -q '"ok"' || fail "API не отвечает на /healthz"
code=$($CURL -o /dev/null -w '%{http_code}' "$BASE/api/me")
[ "$code" = 401 ] || fail "/api/me без входа ответил $code, а не 401"

big=$(head -c 2000000 /dev/zero | tr '\0' 'a' | $CURL -o /dev/null -w '%{http_code}' -X POST \
  -H 'Content-Type: application/json' --data-binary @- "$BASE/api/site-reads")
[ "$big" = 413 ] || fail "тело 2 МБ не отклонено, ответ $big"

headers=$($CURL -D - -o /dev/null "$BASE/")
for name in strict-transport-security content-security-policy x-content-type-options; do
  echo "$headers" | grep -qi "^$name:" || fail "нет заголовка $name"
done
echo "$headers" | grep -qi '^server:' && fail "сервер называет себя в заголовке Server"

# Служебные страницы приложения наружу не открыты: вместо них приходит интерфейс.
$CURL "$BASE/docs" | grep -qi swagger && fail "/docs открыт наружу"
$CURL "$BASE/openapi.json" | grep -q '"openapi"' && fail "/openapi.json открыт наружу"
echo "Проверка после установки пройдена: $BASE"

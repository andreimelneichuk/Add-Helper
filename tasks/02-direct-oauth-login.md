# Задача 02. Авторизация и подключение кабинета Яндекс ID (OAuth 2.0 Web-flow)

- **Статус:** 🟢 **РАЗБЛОКИРОВАНА (Ключи `YANDEX_CLIENT_ID` и `YANDEX_CLIENT_SECRET` получены и прописаны)**
- **Приоритет:** P1 (Критично для авторизации и онбординга)
- **Сложность:** Низкая (Low) — бэкенд-код полностью готов, осталась проверка в браузере
- **Трудозатраты с Claude Code:** 30–45 минут на проверку живого E2E-флоу
- **Зависимости:** Задача 01 (подключение живого DirectPlatform)
- **Блокирует:** Задача 10 (Сдача заказчику под его аккаунт)

---

## 1. Контекст

Приложение в консоли Яндекс ID создано, ключи добавлены в `.env`:
* `YANDEX_CLIENT_ID` настроен.
* `YANDEX_CLIENT_SECRET` настроен.
* Все обходные пути (ручная вставка токенов, dev-заглушки) **больше не нужны**. Система работает по прямому, эталонному **1-Click OAuth 2.0 Web-flow** с шифрованием и PKCE.

---

## 2. Необходимые настройки в консоли Яндекс ID

В настройках приложения Яндекс OAuth должны быть указаны:
1. **Redirect URI (Callback URLs):**
   * `http://localhost:8000/api/auth/yandex/callback` — авторизация пользователя на сайте.
   * `http://localhost:8000/api/accounts/yandex/callback` — привязка кабинета Директа к бизнесу.
2. **Права доступа (Scopes):**
   * `login:info`
   * `login:email`
   * `direct:api`

---

## 3. Что уже реализовано в коде

1. **Адаптер `YandexOAuth` (`src/adhelper/accounts/adapters/yandex_oauth.py`):**
   - Формирование URL авторизации с `code_challenge` (PKCE S256).
   - Обмен `code` на `access_token` и `refresh_token`.
   - Автоматический рефреш токенов при истечении срока жизни.
   - Запрос профиля (`https://login.yandex.ru/info`).
2. **Сервис и хранилище токенов (`AccountsService` & `TokenVault`):**
   - Защита сессий cookie `ah_session` (`HttpOnly`, `SameSite=Lax`, `Secure`).
   - Шифрование access и refresh токенов шифром **AES-256-GCM** в PostgreSQL (`connected_accounts`).
   - Изоляция по `business_id` (чужой пользователь не может получить токен кабинета).
3. **Эндпоинты в `api/accounts.py`:**
   - `/api/auth/yandex/login` $\to$ редирект в Яндекс.
   - `/api/auth/yandex/callback` $\to$ создание пользователя, выдача куки.
   - `/api/businesses/{id}/accounts/connect` $\to$ запрос прав на Директ.
   - `/api/accounts/yandex/callback` $\to$ сохранение токена кабинета.

---

## 4. Критерии готовности (Definition of Done)

- [x] Приложение зарегистрировано в Яндекс ID, ключи внесены в `.env`.
- [x] Бэкенд инициализирует `YandexOAuth` вместо `UnconfiguredProvider`.
- [ ] Пройден сквозной сценарий в браузере: вход на сайт $\to$ создание бизнеса $\to$ привязка кабинета Директа.
- [ ] Токены успешно записаны в PostgreSQL в зашифрованном виде.
- [ ] Адаптер `DirectPlatform` успешно выполняет запросы к кампаниям пользователя.

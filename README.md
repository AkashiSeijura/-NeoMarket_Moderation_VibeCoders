# NeoMarket Moderation

Минимальный сервис Moderation для US-MOD-01 и US-MOD-02.

## Запуск тестов

```powershell
C:\Users\perfe\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m pytest -q
```

## Endpoints

- `POST /api/v1/b2b/events` - основной endpoint приема событий от B2B по OpenAPI Moderation.
- `POST /api/v1/events/product` - совместимый alias под канон MOD-1.
- `POST /api/v1/queue/claim` - взять следующий тикет в работу по OpenAPI Moderation.
- `GET /api/v1/product-moderation/{product_id}` - технический просмотр карточки по товару.

## Авторизация

Межсервисный ключ для B2B-событий: `X-Service-Key`, значение по умолчанию для локального запуска - `b2b-to-mod-key`.

Для `POST /api/v1/queue/claim` используется `Authorization: Bearer <moderator_uuid>`. В полном OpenAPI это bearerAuth; в минимальной реализации токен трактуется как id модератора.

## Конфигурация

- `MODERATION_DB_PATH` - путь к SQLite базе, по умолчанию `moderation.sqlite3`.
- `MODERATION_IN_REVIEW_TIMEOUT_MINUTES` - через сколько минут карточка в `IN_REVIEW` возвращается в `PENDING`, по умолчанию `30`.

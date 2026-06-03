# NeoMarket Moderation

Минимальный сервис Moderation для US-MOD-01, US-MOD-02, US-MOD-03 и US-MOD-04.

## Запуск тестов

```powershell
C:\Users\perfe\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m pytest -q
```

## Endpoints

- `POST /api/v1/b2b/events` - основной endpoint приема событий от B2B по OpenAPI Moderation.
- `POST /api/v1/events/product` - совместимый alias под канон MOD-1.
- `POST /api/v1/queue/claim` - взять следующий тикет в работу по OpenAPI Moderation.
- `POST /api/v1/products/{product_id}/approve` - approve товара модератором по canon-flow MOD-3:
  переводит карточку `IN_REVIEW` -> `MODERATED`; доступен только модератору, за которым закреплена
  карточка; перед approve проверяет актуальные SKU в B2B (`GET /api/v1/products/{product_id}`);
  отправляет `MODERATED` event в B2B (`POST /api/v1/events/moderation`).
- `POST /api/v1/products/{product_id}/decline` - canonical soft-block по product_id.
- `POST /api/v1/tickets/{ticket_id}/block` - OpenAPI-совместимый block endpoint по ticket_id.
- `GET /api/v1/product-moderation/{product_id}` - технический просмотр карточки по товару.

## Авторизация

Межсервисный ключ для B2B-событий: `X-Service-Key`, значение по умолчанию для локального запуска - `b2b-to-mod-key`.

Для moderator endpoints используется `Authorization: Bearer <moderator_uuid>`. В полной схеме это bearerAuth; в минимальной реализации токен трактуется как id модератора.

## Конфигурация

- `MODERATION_DB_PATH` - путь к SQLite базе, по умолчанию `moderation.sqlite3`.
- `MODERATION_IN_REVIEW_TIMEOUT_MINUTES` - через сколько минут карточка в `IN_REVIEW` возвращается в `PENDING`, по умолчанию `30`.
- `B2B_URL` - base URL B2B для исходящих moderation events, по умолчанию `http://b2b:8000`.
- `MOD_TO_B2B_KEY` - `X-Service-Key` для исходящих событий в B2B, по умолчанию `dev-moderation-to-b2b-key`.
- `B2B_TIMEOUT_SECONDS` - timeout исходящего запроса в B2B, по умолчанию `3.0`.

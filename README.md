# NeoMarket Moderation

Минимальный сервис Moderation для US-MOD-01, US-MOD-02, US-MOD-03, US-MOD-04, US-MOD-05 и US-MOD-06.

## Запуск тестов

```powershell
C:\Users\perfe\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m pytest -q
```

## Endpoints

- `POST /api/v1/b2b/events` - основной endpoint приема событий от B2B по OpenAPI Moderation.
- `POST /api/v1/events/product` - совместимый alias под canon-flow MOD-1.
- `POST /api/v1/queue/claim` - взять следующий тикет в работу по OpenAPI Moderation.
- `POST /api/v1/tickets/{ticket_id}/approve` - OpenAPI endpoint одобрения тикета; переводит карточку `IN_REVIEW` -> `MODERATED`, проверяет владельца, актуальные SKU в B2B и отправляет `MODERATED` event в B2B.
- `POST /api/v1/products/{product_id}/approve` - product-based alias из canon-flow MOD-3.
- `POST /api/v1/tickets/{ticket_id}/block` - OpenAPI endpoint блокировки тикета.
- `POST /api/v1/products/{product_id}/decline` - product-based alias из canon-flow MOD-4/MOD-5.
- `GET /api/v1/blocking-reasons` - OpenAPI endpoint справочника причин; поддерживает `hard_block` и `is_active`, по умолчанию отдаёт только активные причины.
- `GET /api/v1/product-blocking-reasons` - совместимый alias для flow-формулировки US-MOD-06.
- `POST/PATCH/DELETE /api/v1/blocking-reasons` - CRUD справочника; `DELETE` выполняет soft-delete через `is_active=false`.
- `GET /api/v1/product-moderation/{product_id}` - технический просмотр карточки по товару.

`decline`/`block` определяет тип блокировки по `reason.hard_block`:

- `hard_block = false` -> статус `BLOCKED` (soft-block, обратимый через повторную модерацию после EDITED).
- `hard_block = true` -> статус `HARD_BLOCKED` (терминальный, штатного API для снятия нет).

Moderation отправляет B2B событие `BLOCKED` с `hard_block=true/false`. Каскад в B2C остается ответственностью B2B; Moderation не вызывает B2C напрямую.

## Приоритет протокола

Если OpenAPI и flow расходятся, реализация следует OpenAPI. Когда flow описывает требование, которого нет в OpenAPI, endpoint добавлен как совместимый alias без замены OpenAPI-контракта.

Примеры:

- approve: OpenAPI описывает `/api/v1/tickets/{ticket_id}/approve`; `/api/v1/products/{product_id}/approve` оставлен как alias для MOD-3.
- block: OpenAPI описывает `/api/v1/tickets/{ticket_id}/block`; `/api/v1/products/{product_id}/decline` оставлен как alias для MOD-4/MOD-5.
- blocking reasons: OpenAPI описывает `/api/v1/blocking-reasons`; `/api/v1/product-blocking-reasons` оставлен как alias для flow-формулировки MOD-6.

## Авторизация

Межсервисный ключ для B2B-событий: `X-Service-Key`, значение по умолчанию для локального запуска - `b2b-to-mod-key`.

Для moderator endpoints используется `Authorization: Bearer <moderator_uuid>`. В полной схеме это bearerAuth; в минимальной реализации токен трактуется как id модератора.

## Конфигурация

- `MODERATION_DB_PATH` - путь к SQLite базе, по умолчанию `moderation.sqlite3`.
- `MODERATION_IN_REVIEW_TIMEOUT_MINUTES` - через сколько минут карточка в `IN_REVIEW` возвращается в `PENDING`, по умолчанию `30`.
- `B2B_URL` - base URL B2B для исходящих moderation events, по умолчанию `http://b2b:8000`.
- `MOD_TO_B2B_KEY` - `X-Service-Key` для исходящих событий в B2B, по умолчанию `dev-moderation-to-b2b-key`.
- `B2B_TIMEOUT_SECONDS` - timeout исходящего запроса в B2B, по умолчанию `3.0`.

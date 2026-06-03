## US-MOD-04

Реализована мягкая блокировка карточки: canonical `POST /api/v1/products/{product_id}/decline` и OpenAPI-совместимый `POST /api/v1/tickets/{ticket_id}/block`. Endpoint проверяет, что карточка в `IN_REVIEW` и закреплена за текущим модератором, валидирует `blocking_reason_id` по seed-справочнику, отклоняет `hard_block=true` причины для soft-flow, сохраняет `field_reports`, переводит карточку в `BLOCKED` и отправляет в B2B canonical-событие `BLOCKED` с `hard_block=false`.

Опубликованный OpenAPI Moderation на 2026-06-03 описывает `/api/v1/tickets/{ticket_id}/block`, `blocking_reason_ids`, `comment` и `field_reports[].field_path/message/severity`; canonical flow описывает `/products/{product_id}/decline`, `blocking_reason_id`, `moderator_comment` и `field_reports[].field_name/sku_id/comment`. По правилу приоритета OpenAPI добавлен `/tickets/{ticket_id}/block`, а `/decline` оставлен как canonical alias для прохождения канон-flow `moderation-flows#soft-block`.

## ADR

Рассмотрел три варианта хранения `field_reports`: отдельная таблица с FK на карточку модерации, JSON-массив в `product_moderation`, event sourcing решений модератора. Выбрана отдельная таблица: по ней проще строить аналитику и фильтровать замечания по `field_name`, при этом payload в B2B остается компактным и формируется только на событие. JSON-массив проще мигрировать при изменении схемы замечаний, но хуже для выборок по отдельным полям. Event sourcing лучше для аудита, но слишком сложен для текущего SQLite-сервиса и не нужен для DoD. Hard-only причину в soft-flow возвращаем как `400 HARD_BLOCK_REASON_NOT_ALLOWED`, а не маршрутизируем в hard-block, чтобы US-MOD-04 не выполнял необратимое действие неявно.

## Tests

```text
C:\Users\perfe\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m pytest -q
..................                                                       [100%]
18 passed, 1 warning in 1.49s
```

Покрытые сценарии:

- `test_soft_block_transitions_to_blocked_with_field_reports`
- `test_soft_block_emits_event_to_b2b`
- `test_soft_block_unknown_reason_returns_400`
- `test_soft_block_others_card_returns_403`
- `test_soft_block_invalid_field_name_returns_400`
- `test_soft_block_hard_only_reason_returns_400_or_routes_to_hard`
- `test_soft_block_openapi_ticket_block_path_accepts_field_path_reports`

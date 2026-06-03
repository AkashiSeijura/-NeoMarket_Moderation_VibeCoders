# US-MOD-03: approve product moderation decision

## Summary

- Added `POST /api/v1/products/{product_id}/approve`.
- Implemented `IN_REVIEW` -> `MODERATED` transition with moderator ownership check.
- Added B2B SKU validation before approval.
- Added outgoing `MODERATED` event to B2B.
- Added pytest coverage for happy path and required unhappy paths.

## Protocol note

- Contract and canon-flow require product-based endpoint `POST /api/v1/products/{product_id}/approve`.
- Current `neomarket-protocols` `moderation/openapi.yaml` also contains ticket-based
  `POST /api/v1/tickets/{ticket_id}/approve`.
- OpenAPI is therefore partially divergent from canon-flow: OpenAPI describes a ticket-based approve,
  while the US-MOD-03 contract requires a product-based approve. This PR follows the US-MOD-03 contract
  and canon-flow MOD-3, because the contract DoD is bound to `product_id`.
- If the protocol team requires it, a separate PR to `neomarket-protocols` should align OpenAPI with the
  product-based approve or explicitly document both variants.

## Idempotency

- B2B already deduplicates moderation events by `idempotency_key`, and publishing the `MODERATED` status
  is safe on redelivery at the `product_id` / `status` level. The outgoing event carries a generated
  `idempotency_key`; no separate outbox table is introduced in this task.

## ADR

Для доставки события `MODERATED` в B2B рассмотрены три варианта: синхронный POST из обработчика approve,
outbox-pattern с фоновой отправкой и event-bus. Для текущей реализации выбран синхронный POST, потому что
он проще, быстрее реализуется и напрямую соответствует canon-flow US-MOD-03. Надёжность обеспечивается
транзакцией: если B2B не принимает событие, approve возвращает 500, изменения в `product_moderation`
откатываются, и карточка остаётся в `IN_REVIEW` для повторной попытки. Outbox-pattern надёжнее при долгих
отказах B2B, но требует отдельной таблицы outbox, фонового воркера и дополнительной идемпотентной
обработки, что избыточно для текущего учебного сервиса. Event-bus также отложен как более инфраструктурно
сложный вариант.

## Tests

```text
python -m pytest -q
.......................                                                  [100%]
23 passed, 1 warning in 1.25s
```

Покрытые сценарии US-MOD-03:

- `test_approve_transitions_to_moderated_and_emits_event`
- `test_approve_others_card_returns_403`
- `test_approve_after_edited_returns_409`
- `test_approve_without_sku_returns_409`
- `test_approve_b2b_event_failure_keeps_card_in_review`

## Definition of Done

- `POST /api/v1/products/{product_id}/approve` реализован.
- `approve_transitions_to_moderated_and_emits_event` проходит.
- `approve_others_card_returns_403` проходит.
- `approve_after_edited_returns_409` проходит.
- `approve_without_sku_returns_409` проходит.
- При ошибке отправки события в B2B локальный approve откатывается.
- `README.md` обновлён.
- `PR_DESCRIPTION.md` содержит ADR.
- Все существующие тесты проходят (US-MOD-01/02/04 не сломаны).

---

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

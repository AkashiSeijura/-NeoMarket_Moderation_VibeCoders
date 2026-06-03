# US-MOD-03: approve product moderation decision

## Summary

- Added OpenAPI-priority `POST /api/v1/tickets/{ticket_id}/approve`.
- Kept canon-flow alias `POST /api/v1/products/{product_id}/approve`.
- Implemented `IN_REVIEW` -> `MODERATED` transition with moderator ownership check.
- Added B2B SKU validation before approval.
- Added outgoing `MODERATED` event to B2B.
- Preserved merged US-MOD-04/US-MOD-05 block behavior.

## Protocol note

OpenAPI Moderation describes ticket-based approve:

- `POST /api/v1/tickets/{ticket_id}/approve`
- body field `comment`
- response as `TicketResponse`

The flow document describes product-based approve:

- `POST /api/v1/products/{product_id}/approve`
- product id based MOD-3 scenario

The conflict is resolved by OpenAPI priority: ticket-based approve is implemented as the primary endpoint. Product-based approve is left as a compatibility alias because the flow describes it and OpenAPI does not provide a product-id variant.

For block decisions the same rule is used: OpenAPI `/api/v1/tickets/{ticket_id}/block` is primary, while flow `/api/v1/products/{product_id}/decline` remains an alias. `hard_block=true` reasons route to `HARD_BLOCKED`; `hard_block=false` reasons route to `BLOCKED`.

## Idempotency

B2B already deduplicates moderation events by `idempotency_key`, and publishing the `MODERATED` status is safe on redelivery at the `product_id` / `status` level. The outgoing event carries a generated `idempotency_key`; no separate outbox table is introduced in this task.

## ADR

Для доставки события `MODERATED` в B2B рассмотрены три варианта: синхронный POST из обработчика approve, outbox-pattern с фоновой отправкой и event-bus. Для текущей реализации выбран синхронный POST, потому что он проще, быстрее реализуется и напрямую соответствует учебному flow. Надежность обеспечивается транзакцией: если B2B не принимает событие, approve возвращает 500, изменения в `product_moderation` откатываются, и карточка остается в `IN_REVIEW` для повторной попытки. Outbox-pattern надежнее при долгих отказах B2B, но требует отдельной таблицы, фонового воркера и дополнительной идемпотентной обработки. Event-bus также отложен как более инфраструктурно сложный вариант.

## Tests

```text
...............................                                          [100%]
31 passed, 1 warning in 2.09s
```

Покрытые сценарии US-MOD-03:

- `test_approve_transitions_to_moderated_and_emits_event`
- `test_openapi_ticket_approve_path_accepts_comment_and_returns_ticket`
- `test_approve_others_card_returns_403`
- `test_approve_after_edited_returns_409`
- `test_approve_without_sku_returns_409`
- `test_approve_b2b_event_failure_keeps_card_in_review`

## Definition of Done

- `POST /api/v1/tickets/{ticket_id}/approve` реализован по OpenAPI.
- `POST /api/v1/products/{product_id}/approve` оставлен как flow alias.
- При approve проверяются `IN_REVIEW` и назначенный модератор.
- Перед approve проверяются актуальные SKU в B2B.
- При ошибке отправки события в B2B локальный approve откатывается.
- `HARD_BLOCKED` защищен от mutating endpoints, включая approve.
- `README.md` обновлен.
- `PR_DESCRIPTION.md` содержит ADR.

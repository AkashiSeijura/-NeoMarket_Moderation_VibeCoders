# US-MOD-05: hard block product moderation decision

## Summary

- Extended `POST /api/v1/products/{product_id}/decline` to route `hard_block=true` reasons into `HARD_BLOCKED`.
- `HARD_BLOCKED` is a terminal status: all mutating moderation endpoints return 403 for such cards.
- Added `BLOCKED` event emission with `hard_block=true` for B2B on hard-block decisions.
- Incoming `EDITED` events on `HARD_BLOCKED` cards are silently ignored (idempotent, no state change).
- `DELETED` events remove `HARD_BLOCKED` cards from Moderation as expected.
- Added pytest coverage for required happy and unhappy paths (7 tests).

## Protocol note

No new endpoint created. Canon-flow specifies that hard-block and soft-block share
`POST /api/v1/products/{product_id}/decline`; routing is determined by the `blocking_reason.hard_block`
flag in the seed catalogue.

## ADR

Для гарантии необратимости рассматривались три варианта: терминальный enum-статус `HARD_BLOCKED` с
проверкой на каждом мутирующем endpoint, отдельный флаг `is_terminal` в схеме и перенос `HARD_BLOCKED`
карточек в отдельную архивную таблицу. Выбран терминальный enum-статус, потому что он даёт один источник
правды, не требует новой схемы и хорошо ложится на текущую state machine `product_moderation`. Отдельный
`is_terminal` повышает риск рассинхронизации двух полей, а архивная таблица усложняет аудит,
JOIN-запросы и emergency data-fix. При экстренной разблокировке через суперадминский data-fix достаточно
изменить один статус с сохранением audit log, но в штатном API обратного перехода нет. Риск случайной
правки снижается явной проверкой `HARD_BLOCKED` в каждом мутирующем endpoint и тестами на
терминальность.

## Tests

```text
python -m pytest -q
.........................                                                [100%]
25 passed, 1 warning in 1.59s
```

Покрытые сценарии US-MOD-05:

- `test_hard_block_transitions_to_terminal_and_emits_event`
- `test_hard_block_event_carries_hard_block_true`
- `test_any_modify_on_hard_blocked_returns_403`
- `test_edited_event_on_hard_blocked_is_ignored`
- `test_deleted_event_removes_hard_blocked`
- `test_soft_block_still_uses_blocked_and_hard_block_false`
- `test_hard_block_b2b_failure_rolls_back_local_changes`

## Definition of Done

- `POST /api/v1/products/{product_id}/decline` поддерживает `hard_block=true` причины.
- `hard_block_transitions_to_terminal_and_emits_event` проходит.
- `hard_block_event_carries_hard_block_true` проходит.
- `any_modify_on_hard_blocked_returns_403` проходит.
- `edited_event_on_hard_blocked_is_ignored` проходит.
- `deleted_event_removes_hard_blocked` проходит.
- Soft-block flow с `hard_block=false` не сломан.
- B2B event для hard-block содержит `BLOCKED` + `hard_block=true`.
- `README.md` обновлён.
- `PR_DESCRIPTION.md` содержит ADR.
- Все тесты проходят (US-MOD-01/02/04 не сломаны).

---

## US-MOD-04

Реализована мягкая блокировка карточки: canonical `POST /api/v1/products/{product_id}/decline` и OpenAPI-совместимый `POST /api/v1/tickets/{ticket_id}/block`. Endpoint проверяет, что карточка в `IN_REVIEW` и закреплена за текущим модератором, валидирует `blocking_reason_id` по seed-справочнику, отклоняет `hard_block=true` причины для soft-flow, сохраняет `field_reports`, переводит карточку в `BLOCKED` и отправляет в B2B canonical-событие `BLOCKED` с `hard_block=false`.

Опубликованный OpenAPI Moderation на 2026-06-03 описывает `/api/v1/tickets/{ticket_id}/block`, `blocking_reason_ids`, `comment` и `field_reports[].field_path/message/severity`; canonical flow описывает `/products/{product_id}/decline`, `blocking_reason_id`, `moderator_comment` и `field_reports[].field_name/sku_id/comment`. По правилу приоритета OpenAPI добавлен `/tickets/{ticket_id}/block`, а `/decline` оставлен как canonical alias для прохождения канон-flow `moderation-flows#soft-block`.

## ADR (US-MOD-04)

Рассмотрел три варианта хранения `field_reports`: отдельная таблица с FK на карточку модерации, JSON-массив в `product_moderation`, event sourcing решений модератора. Выбрана отдельная таблица: по ней проще строить аналитику и фильтровать замечания по `field_name`, при этом payload в B2B остается компактным и формируется только на событие. JSON-массив проще мигрировать при изменении схемы замечаний, но хуже для выборок по отдельным полям. Event sourcing лучше для аудита, но слишком сложен для текущего SQLite-сервиса и не нужен для DoD. Hard-only причину в soft-flow ранее возвращали как `400 HARD_BLOCK_REASON_NOT_ALLOWED`; после US-MOD-05 она маршрутизируется в HARD_BLOCKED.

## Tests (US-MOD-04)

```text
..................                                                       [100%]
18 passed, 1 warning in 1.49s
```

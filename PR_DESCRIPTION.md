# US-MOD-06: справочник причин блокировки

## Summary

- Добавлен read-API `GET /api/v1/blocking-reasons` по OpenAPI Moderation.
- Добавлен совместимый alias `GET /api/v1/product-blocking-reasons` для flow-формулировки US-MOD-06.
- Добавлены фильтры `hard_block` и `is_active`; по умолчанию API отдаёт только активные причины.
- Добавлены CRUD-операции справочника через admin-like API `POST/PATCH/DELETE /api/v1/blocking-reasons`.
- `DELETE` не удаляет запись физически, а переводит её в `is_active=false`, чтобы исторические BLOCKED/HARD_BLOCKED карточки сохраняли ссылку.

## Protocol note

OpenAPI Moderation сейчас описывает `GET /api/v1/blocking-reasons`, а не `GET /product-blocking-reasons`. Так как при конфликте приоритет у OpenAPI, основной endpoint реализован как `/api/v1/blocking-reasons`. Для совместимости с flow-именованием добавлен alias `/api/v1/product-blocking-reasons` с тем же ответом и фильтрами.

## ADR

Рассмотрены три варианта хранения причин: enum в коде, таблица в БД с CRUD-админкой и i18n-каталог. Enum проще для разработки, но плохо подходит для добавления новой причины без релиза и не решает исторические ссылки при удалении. i18n-каталог удобен для многоязычности, но как первичное хранилище усложняет CRUD и ссылочную целостность. Выбрана таблица в БД: новую причину можно добавить через admin-like API, исторические карточки держат FK/id на запись, а `description`/будущие локализации можно расширять без изменения аналитического id.

## Tests

```text
======================== 35 passed, 1 warning in 2.34s ========================
```

Покрытые сценарии US-MOD-06:

- `test_list_returns_active_reasons`
- `test_inactive_reasons_not_visible`
- `test_hard_block_filter_returns_matching_type`
- `test_referenced_reason_cannot_be_deleted`

## Definition of Done

- Активные причины возвращаются с полями `id`, `code`, `title`, `description`, `hard_block`, `is_active`.
- Деактивированные причины скрыты при дефолтном `is_active=true`.
- Фильтр `hard_block=true/false` работает для hard/soft причин.
- Причины не удаляются физически; `DELETE` выполняет soft-delete и не ломает исторические карточки.

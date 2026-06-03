## US-MOD-02

Реализована выдача следующей карточки модерации через `POST /api/v1/queue/claim` по OpenAPI Moderation. Endpoint атомарно возвращает просроченные `IN_REVIEW` карточки в `PENDING`, проверяет, что у модератора нет активной карточки, выбирает верхнюю карточку очереди по `queue_priority ASC, date_created ASC`, переводит ее в `IN_REVIEW` и закрепляет за модератором. Если очередь пуста, возвращается `204`; если модератор уже держит активную карточку, возвращается `409`.

## ADR

Рассмотрел три варианта защиты от конкурентного доступа: блокировка на уровне БД (`SELECT FOR UPDATE SKIP LOCKED` в PostgreSQL / сериализация writer-транзакции в SQLite), внешний lock в Redis и отдельный queue-сервис. Выбран DB-level lock: в текущем сервисе нет PostgreSQL и Redis, поэтому `BEGIN IMMEDIATE` в SQLite дает атомарный выбор+update без дополнительных зависимостей и проще отлаживается по одному месту хранения состояния. Redis добавил бы отказоустойчивые нюансы с TTL lock-а и рассинхронизацией с БД, а отдельный queue-сервис слишком тяжелый для текущего объема. Если модератор пропал, карточка автоматически возвращается в `PENDING` после `MODERATION_IN_REVIEW_TIMEOUT_MINUTES` минут, по умолчанию 30.

## Tests

```text
C:\Users\perfe\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m pytest -q
...........                                                              [100%]
11 passed, 1 warning in 2.28s
```

Покрыты сценарии:

- `next_returns_oldest_pending`
- `concurrent_two_moderators_get_different_cards`
- `empty_queue_returns_204`
- `moderator_already_has_in_review_returns_409`
- `expired_in_review_returns_to_queue`

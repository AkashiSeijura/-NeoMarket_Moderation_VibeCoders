# NeoMarket Moderation

Минимальный сервис Moderation для US-MOD-01.

## Запуск тестов

```powershell
C:\Users\perfe\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m pytest -q
```

## Endpoint

- `POST /api/v1/b2b/events` — основной endpoint по OpenAPI Moderation.
- `POST /api/v1/events/product` — совместимый алиас под канон MOD-1.

Межсервисный ключ: `X-Service-Key`, значение по умолчанию для локального запуска — `b2b-to-mod-key`.

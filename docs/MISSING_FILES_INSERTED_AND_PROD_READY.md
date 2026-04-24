# Missing files inserted + production ready

Дата: 2026-04-24

## Что сделано

В этот проект вставлены недостающие production-файлы и обновлены основные backend-файлы.

## Добавлены/обновлены файлы

```text
backend/app/core/settings.py

backend/app/services/ai/ai_service.py
backend/app/services/ai/openai_client.py
backend/app/services/ai/prompts.py
backend/app/services/ai/lead_extractor.py

backend/app/services/memory/redis_memory.py
backend/app/services/state/fsm.py
backend/app/services/db/postgres.py
backend/app/services/db/models.py
backend/app/services/db/init_db.py
backend/app/services/security/rate_limiter.py
backend/app/services/logging/safe_logger.py

backend/app/channels/telegram.py
backend/app/channels/whatsapp.py
backend/app/channels/health.py

backend/.env.example
backend/requirements.txt
```

## Новая логика

```text
Telegram / WhatsApp
        ↓
unified webhook layer
        ↓
AI service
        ↓
OpenAI retry/fallback
        ↓
Redis memory + FSM
        ↓
PostgreSQL leads
        ↓
reply to user
```

## Production hardening

- rate limiting
- OpenAI retry
- OpenAI timeout
- fallback replies
- safe logging with masking
- message length limit
- health endpoint
- DATABASE_URL hotfix for Railway/Supabase
- DB errors no longer crash bot

## Railway variables

```env
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4o-mini
OPENAI_TIMEOUT_SECONDS=25
OPENAI_MAX_RETRIES=2

TELEGRAM_BOT_TOKEN=...

DATABASE_URL=postgresql://postgres:password@host:5432/postgres

REDIS_URL=redis://default:password@host:6379

AI_MEMORY_MAX_HISTORY=12
AI_MEMORY_TTL_SECONDS=86400
AI_STATE_TTL_SECONDS=86400

RATE_LIMIT_MESSAGES=20
RATE_LIMIT_WINDOW_SECONDS=60
MAX_USER_MESSAGE_CHARS=3000

ENABLE_LEAD_EXTRACTION=true
ENABLE_DB_LEADS=true

ENV=production
LOG_LEVEL=info
```

## Railway settings

Root Directory:

```text
backend
```

Start Command:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

## GitHub commands

```bash
git add .
git commit -m "Insert production AI sales bot files"
git push origin main
```

Если новый репозиторий:

```bash
git init
git branch -M main
git add .
git commit -m "Initial production AI sales bot"
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

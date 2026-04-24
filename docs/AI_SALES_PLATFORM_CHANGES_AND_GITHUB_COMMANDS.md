# Siteformo AI Sales Platform — готовая версия проекта

Дата сборки: 2026-04-24

## Что сделано

Проект собран как полностью заменяемая версия согласно задачам выше:

```text
Telegram + WhatsApp
        ↓
единые channel webhooks
        ↓
app.services.ai.ai_service.generate_ai_reply()
        ↓
OpenAI GPT
        ↓
Redis memory
        ↓
FSM sales funnel
        ↓
PostgreSQL leads
        ↓
ответ пользователю
```

## Добавлено / обновлено

```text
backend/app/services/ai/ai_service.py
backend/app/services/ai/prompts.py
backend/app/services/ai/lead_extractor.py
backend/app/services/memory/redis_memory.py
backend/app/services/state/fsm.py
backend/app/services/db/postgres.py
backend/app/services/db/models.py
backend/app/services/db/init_db.py
backend/app/channels/telegram.py
backend/app/channels/whatsapp.py
backend/app/channels/health.py
backend/.env.example
backend/requirements.txt
```

## Возможности

- Telegram webhook отвечает через GPT.
- WhatsApp/Twilio webhook отвечает через GPT.
- Оба канала используют одну AI-логику.
- Redis хранит историю диалога и этап воронки.
- PostgreSQL сохраняет лиды.
- Есть fallback: если Redis не настроен локально, память временно хранится in-memory.
- Если `DATABASE_URL` не задан, проект не падает локально.

## Railway ENV

Добавь переменные:

```env
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4o-mini
TELEGRAM_BOT_TOKEN=...
DATABASE_URL=...
REDIS_URL=...
```

Можно вместо `REDIS_URL`:

```env
REDIS_HOST=...
REDIS_PORT=6379
REDIS_PASSWORD=...
```

## Railway deploy

Root Directory:

```text
backend
```

Start Command:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

## Webhook URLs

```text
https://YOUR-RAILWAY-DOMAIN/channels/telegram/webhook
https://YOUR-RAILWAY-DOMAIN/channels/whatsapp/webhook
https://YOUR-RAILWAY-DOMAIN/channels/health
```

## Команды для GitHub

Если репозиторий уже подключён:

```bash
git status
git add .
git commit -m "Implement unified AI sales bot for Telegram and WhatsApp"
git push origin main
```

Если это новый репозиторий:

```bash
git init
git branch -M main
git add .
git commit -m "Initial AI sales platform backend"
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

Если нужно заменить remote:

```bash
git remote set-url origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

## В ZIP специально не включены

```text
.git
venv
__pycache__
локальные .db / .sqlite файлы
node_modules
```

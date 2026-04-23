# SiteFormo fix report — 2026-04-23

## Что проверено
- распакован проект и просмотрена структура backend
- прочитаны все ключевые `.md` файлы в корне, `docs/`, `backend/README.md`, `vercel-demo-proxy/README.md`
- сверена фактическая реализация с описанной логикой каналов
- выполнена компиляционная проверка Python-модулей
- выполнен локальный smoke-test маршрутов FastAPI

## Что исправлено
1. `backend/app/core/config.py`
   - добавлена загрузка `.env` и `.env.local`
   - пустые строковые env-значения теперь нормализуются
   - пустой `DATABASE_URL` безопасно откатывается на локальный SQLite
   - приведены дефолтные owner/bypass email к `klon97048@gmail.com`
   - `WHATSAPP_PROVIDER` нормализуется к lowercase

2. `backend/.env.example`
   - синхронизирован с текущей логикой Twilio/Telegram echo-бота
   - owner/bypass/smtp email приведены к `klon97048@gmail.com`

3. `backend/.env.railway.example`
   - добавлен полный и чистый шаблон Railway переменных
   - включены Twilio, Telegram, SMTP и DATABASE_URL

4. `README.md`
   - обновлён под фактическую текущую архитектуру
   - добавлены правильные Railway команды
   - добавлены правильные webhook URL

## Что уже было правильно и сохранено
- `backend/app/main.py` подключает `channel_router` и `api_router`
- `backend/app/api/channel_routes.py` содержит:
  - `GET /channels/health`
  - `POST /channels/web-chat/message`
  - `POST /channels/telegram/webhook`
  - `GET /channels/whatsapp/webhook`
  - `POST /channels/whatsapp/webhook`
- `backend/app/services/chatbot_service.py` реализует echo-логику
- WhatsApp Twilio webhook отвечает через TwiML XML

## Локальная проверка
Команды:

```bash
cd backend
python -m py_compile $(find app -name '*.py')
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Проверено:
- `/docs`
- `/health`
- `/channels/health`
- `POST /channels/web-chat/message`
- `POST /channels/whatsapp/webhook` с form-data

## Что заполнить в Railway
Минимум:
- `PUBLIC_BASE_URL=https://siteformo-ai-platform-production.up.railway.app`
- `APPROVAL_SECRET=...`
- `OWNER_EMAIL=klon97048@gmail.com`
- `PAYMENT_APPROVAL_BYPASS_EMAILS=klon97048@gmail.com`
- `WHATSAPP_PROVIDER=twilio`
- `WHATSAPP_TWILIO_NUMBER=whatsapp:+14155238886`
- `TELEGRAM_BOT_TOKEN=...`
- `TELEGRAM_BOT_USERNAME=...`

Опционально:
- `DATABASE_URL=...` (Railway Postgres)
- `OPENAI_API_KEY=...`
- `SMTP_*`

## Команды для push в GitHub
```bash
git add .
git commit -m "Fix config defaults, sync env templates, and stabilize Railway setup"
git push origin main
```

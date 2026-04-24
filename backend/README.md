# SiteFormo AI Sales Platform

Текущая сборка приведена к режиму простого echo-бота по каналам:
- Telegram webhook работает как echo-бот
- WhatsApp Twilio Sandbox работает как echo-бот
- Web chat работает как echo-бот
- FastAPI endpoint для WhatsApp: `/channels/whatsapp/webhook`
- Health check: `/channels/health`

Ответ во всех каналах сейчас такой:
- `привет` → `Ты написал: привет`

## Что проверено и приведено в порядок
- прочитаны и сверены инструкции из `.md` файлов проекта
- сохранён основной API заказов из `backend/app/api/routes.py`
- сохранены и подключены channel routes из `backend/app/api/channel_routes.py`
- Twilio WhatsApp webhook оставлен в формате `POST form-data -> TwiML XML`
- Telegram webhook оставлен как простой echo-ответ
- Web chat webhook оставлен как простой echo-ответ
- настройки окружения нормализованы
- пустой `DATABASE_URL` теперь безопасно откатывается на локальный SQLite
- `.env`, `.env.local`, `.env.railway` шаблоны синхронизированы
- owner/bypass email приведены к `klon97048@gmail.com` согласно логике проекта и md-документам

## Railway start command

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

## Railway build command

```bash
pip install -r requirements.txt
```

## Telegram webhook

После заполнения `TELEGRAM_BOT_TOKEN`:

```bash
curl -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://siteformo-ai-platform-production.up.railway.app/channels/telegram/webhook"}'
```

## Twilio Sandbox webhook

В Twilio Sandbox → **When a message comes in**:

```text
https://siteformo-ai-platform-production.up.railway.app/channels/whatsapp/webhook
```

Method:

```text
POST
```

## Railway variables

Смотри шаблон:

```text
backend/.env.railway.example
```

Критичные поля:
- `PUBLIC_BASE_URL`
- `DATABASE_URL` (если используешь Railway Postgres)
- `APPROVAL_SECRET`
- `OWNER_EMAIL`
- `PAYMENT_APPROVAL_BYPASS_EMAILS`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_BOT_USERNAME`
- `WHATSAPP_PROVIDER=twilio`
- `WHATSAPP_TWILIO_NUMBER=whatsapp:+14155238886`

Для текущего Twilio echo-ответа `ACCOUNT_SID` и `AUTH_TOKEN` не обязательны, потому что ответ уходит напрямую через webhook как TwiML.

## Guided web-chat вместо свободного /chat

Продажный бот переведён на сценарный flow с кнопками:

- `POST /channels/web-chat/start` — старт/восстановление сессии.
- `POST /channels/web-chat` — отправка выбранного варианта или контакта.
- `POST /channels/web-chat/message` — legacy alias, теперь тоже guided flow.
- `POST /chat` — отключён, возвращает `410 Gone`.

Сессии сохраняются в `conversation_sessions`, сообщения — в `conversation_messages`, финальные лиды — в `leads`.

Локальная demo-страница виджета: `/static/web-chat-demo.html`.

# SiteFormo backend

Текущая рабочая конфигурация для этой сборки:
- Telegram = echo bot
- WhatsApp = Twilio Sandbox webhook echo bot
- Web chat = echo bot

## Основные endpoints
- `GET /health`
- `GET /channels/health`
- `POST /channels/telegram/webhook`
- `POST /channels/whatsapp/webhook`
- `POST /channels/web-chat/message`

## Локальный запуск
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.local.example .env
uvicorn app.main:app --reload
```

## Railway start command
```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

## Railway variables
Минимально нужны:
- `PUBLIC_BASE_URL`
- `APPROVAL_SECRET`
- `OWNER_EMAIL`
- `PAYMENT_APPROVAL_BYPASS_EMAILS`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_BOT_USERNAME`
- `WHATSAPP_PROVIDER=twilio`
- `WHATSAPP_PUBLIC_NUMBER=whatsapp:+14155238886`
- `WHATSAPP_TWILIO_NUMBER=whatsapp:+14155238886`
- `DATABASE_URL` (если используешь Railway Postgres)

Для текущего Twilio echo-ответа `ACCOUNT_SID` и `AUTH_TOKEN` не обязательны, потому что ответ уходит напрямую через webhook как TwiML.

## Проверка
### Telegram
После установки webhook отправь боту любое сообщение.
Ожидаемо:
```text
Ты написал: привет
```

### WhatsApp
После подключения к Sandbox отправь в WhatsApp любое сообщение.
Ожидаемо:
```text
Ты написал: привет
```

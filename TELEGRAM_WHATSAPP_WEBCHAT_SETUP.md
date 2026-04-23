# Telegram + WhatsApp + Web Chat setup

## Что настроено в этой сборке
- Telegram webhook: `/channels/telegram/webhook`
- WhatsApp webhook для Twilio Sandbox: `/channels/whatsapp/webhook`
- Web chat endpoint: `/channels/web-chat/message`
- Все каналы сейчас работают как echo-бот:
  - вход: `привет`
  - ответ: `Ты написал: привет`

## Railway
Запусти backend командой:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Заполни переменные из файла `backend/.env.railway.example`.

## Telegram
1. Создай бота через `@BotFather`.
2. Возьми токен.
3. Заполни в Railway:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_BOT_USERNAME`
4. Поставь webhook:

```bash
curl -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://siteformo-ai-platform-production.up.railway.app/channels/telegram/webhook"}'
```

## WhatsApp via Twilio Sandbox
1. Открой Twilio Console → Messaging → Try it out → Send a WhatsApp message.
2. Подключи Sandbox командой `join ...` из Twilio.
3. В поле webhook URL укажи:

```text
https://siteformo-ai-platform-production.up.railway.app/channels/whatsapp/webhook
```

4. Метод: `POST`.
5. В Railway поставь:
   - `WHATSAPP_PROVIDER=twilio`
   - `WHATSAPP_PUBLIC_NUMBER=whatsapp:+14155238886`
   - `WHATSAPP_TWILIO_NUMBER=whatsapp:+14155238886`

## Проверка
### Web chat
```bash
curl -X POST http://127.0.0.1:8000/channels/web-chat/message \
  -H "Content-Type: application/json" \
  -d '{"user_id":"demo-user-1","text":"привет"}'
```

### WhatsApp Twilio local test
```bash
curl -X POST http://127.0.0.1:8000/channels/whatsapp/webhook \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "From=whatsapp:+1234567890" \
  --data-urlencode "Body=привет"
```

Ожидаемый XML-ответ содержит:

```text
Ты написал: привет
```

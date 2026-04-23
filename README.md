# SiteFormo AI Sales Platform

Готовая версия проекта для текущей задачи:
- Telegram webhook работает как echo-бот
- WhatsApp webhook работает с Twilio Sandbox
- FastAPI endpoint для WhatsApp: `/channels/whatsapp/webhook`
- Health check: `/channels/health`
- Swagger: `/docs`

## Что сейчас делает бот
Любое входящее сообщение получает ответ:

```text
Ты написал: <твой текст>
```

Это сделано одинаково для:
- Telegram
- WhatsApp (Twilio Sandbox)
- web chat

## Ключевые исправления
- исправлен вызов `ChatbotService.process_message(...)`
- WhatsApp webhook адаптирован под `application/x-www-form-urlencoded` от Twilio
- добавлен XML TwiML-ответ для мгновенного автоответа в WhatsApp
- `requirements.txt` переведён в UTF-8
- добавлен `python-multipart` для чтения формы webhook
- очищены и упрощены `.env`-шаблоны

## Railway start command
```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

## Telegram webhook
```bash
curl -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://siteformo-ai-platform-production.up.railway.app/channels/telegram/webhook"}'
```

## Twilio Sandbox webhook
В Twilio Sandbox for WhatsApp укажи:

```text
https://siteformo-ai-platform-production.up.railway.app/channels/whatsapp/webhook
```

HTTP method: `POST`

## Railway variables
Смотри файл `backend/.env.railway.example`.

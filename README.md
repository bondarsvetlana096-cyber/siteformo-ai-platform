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

## AI Sales Bot Layer

Проект обновлён под единую AI-логику для Telegram и WhatsApp.

Подробности:

```text
docs/AI_SALES_PLATFORM_CHANGES_AND_GITHUB_COMMANDS.md
```

## Production AI Sales Bot

В проект вставлены недостающие production-файлы для Telegram + WhatsApp AI sales bot.

Документация:

```text
docs/MISSING_FILES_INSERTED_AND_PROD_READY.md
```


## CRM + Notifications

Добавлены API лидов, мини-админка `/admin`, статусы лидов и Telegram-уведомления владельцу.

Документация:

```text
docs/CRM_AND_NOTIFICATIONS.md
```

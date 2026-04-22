# Telegram + WhatsApp + Web Chat setup

## Что уже добавлено
- единый движок опросника для 3 каналов;
- Telegram webhook endpoint: `/channels/telegram/webhook`;
- WhatsApp webhook verify + inbound endpoint: `/channels/whatsapp/webhook`;
- Web chat endpoint для сайта: `/channels/web-chat/message`;
- сохранение сессий и сообщений в БД;
- создание заказа через существующий `IntakeService`.

## .env
Добавь в `.env`:

```env
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.1-mini
TELEGRAM_BOT_TOKEN=
WHATSAPP_API_KEY=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_WEBHOOK_VERIFY_TOKEN=
PUBLIC_BASE_URL=https://your-domain.com
```

## Telegram
1. Создай бота через `@BotFather`.
2. Возьми токен.
3. Поставь webhook:

```bash
curl -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://your-domain.com/channels/telegram/webhook"}'
```

## WhatsApp Cloud API
1. Создай app в Meta Developers.
2. Подключи WhatsApp product.
3. Возьми permanent/system-user token.
4. Укажи callback URL:
   `https://your-domain.com/channels/whatsapp/webhook`
5. Укажи verify token = `WHATSAPP_WEBHOOK_VERIFY_TOKEN`.
6. Подпишись минимум на поле `messages`.

## Web chat на сайте
Кнопка может открывать простой виджет или твой front-end chat.
Запросы идут так:

```bash
curl -X POST https://your-domain.com/channels/web-chat/message \
  -H "Content-Type: application/json" \
  -d '{"user_id":"demo-user-1","text":"/start"}'
```

## Первый тест
1. `/channels/health`
2. `/channels/web-chat/message`
3. Telegram `/start`
4. WhatsApp message `start`

## Что редактировать дальше
Если хочешь менять только опросник, редактируй один файл:
- `backend/app/services/chatbot_service.py`

Именно там хранятся:
- шаги сценария;
- тексты вопросов;
- переходы между шагами;
- момент создания заявки.


## Launch behavior and bypass emails

Use `GET /channels/launch-links` to render site buttons for Telegram and WhatsApp.

- WhatsApp should use the returned `wa.me` link with prefilled text.
- Telegram should use the returned `t.me/<bot>?start=siteformo_intake` link.
- Payment approval bypass emails are configured via `PAYMENT_APPROVAL_BYPASS_EMAILS`, for example `klon97048@gmail.com`.

# Channel patch points

Текущая сборка уже поддерживает:
- Telegram webhook
- WhatsApp webhook через Twilio Sandbox
- простой echo-ответ во всех каналах

Если позже будешь возвращать полноценный AI-диалог, меняй в первую очередь:
- `backend/app/services/chatbot_service.py`
- `backend/app/api/channel_routes.py`
- `backend/app/services/whatsapp_service.py`
- `backend/app/services/telegram_service.py`

## Для Twilio сейчас важно
- endpoint: `/channels/whatsapp/webhook`
- метод: `POST`
- формат: `application/x-www-form-urlencoded`
- ответ: TwiML XML

## Для Meta Cloud API при будущем переключении
Оставлены переменные:
- `WHATSAPP_PROVIDER=meta`
- `WHATSAPP_API_KEY`
- `WHATSAPP_PHONE_NUMBER_ID`
- `WHATSAPP_WEBHOOK_VERIFY_TOKEN`

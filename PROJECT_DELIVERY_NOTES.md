# Delivery notes

## Done
- Fixed Telegram/WhatsApp webhook handler calls to `ChatbotService.process_message(...)`
- Switched WhatsApp webhook handling to support Twilio Sandbox POST form payloads
- Added TwiML XML reply for WhatsApp echo responses
- Simplified `ChatbotService` to current requested echo behavior
- Cleaned environment templates
- Added Railway environment template
- Converted `backend/requirements.txt` to UTF-8 and added `python-multipart`

## Expected behavior now
- Telegram: any message -> `Ты написал: <текст>`
- WhatsApp Twilio Sandbox: any message -> `Ты написал: <текст>`
- Web chat: any message -> `Ты написал: <текст>`

## Main files changed
- `backend/app/api/channel_routes.py`
- `backend/app/services/chatbot_service.py`
- `backend/app/services/whatsapp_service.py`
- `backend/app/core/config.py`
- `backend/app/main.py`
- `backend/.env.example`
- `backend/.env.local.example`
- `backend/.env.railway.example`
- `backend/requirements.txt`
- `README.md`
- `backend/README.md`
- `TELEGRAM_WHATSAPP_WEBCHAT_SETUP.md`

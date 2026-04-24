# CRM + Telegram notifications

Дата: 2026-04-24

## Что добавлено

```text
backend/app/api/auth.py
backend/app/api/leads.py
backend/app/api/admin.py
backend/app/services/notifications/telegram_notifier.py
backend/app/services/db/migrations.py
```

## Возможности

- Мини-админка: `/admin`
- API лидов: `/api/leads/`
- Последние лиды: `/api/leads/latest`
- Обновление статуса: `PATCH /api/leads/{lead_id}/status?status=contacted`
- Telegram-уведомления владельцу при новом лиде
- Статусы: `new`, `contacted`, `qualified`, `closed`, `lost`

## Новые Railway переменные

```env
ADMIN_API_KEY=change-me-long-random-secret
OWNER_TELEGRAM_CHAT_ID=
ENABLE_OWNER_NOTIFICATIONS=true
```

## Как узнать OWNER_TELEGRAM_CHAT_ID

1. Напиши своему Telegram-боту любое сообщение.
2. Открой:

```text
https://api.telegram.org/botYOUR_TELEGRAM_BOT_TOKEN/getUpdates
```

3. Найди:

```json
"chat": {"id": 123456789}
```

4. Добавь в Railway:

```env
OWNER_TELEGRAM_CHAT_ID=123456789
```

## GitHub

```bash
git add .
git commit -m "Add CRM leads admin and owner notifications"
git push origin main
```

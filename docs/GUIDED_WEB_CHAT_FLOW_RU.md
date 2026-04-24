# SiteFormo guided web-chat / квиз-бот

Свободный чат больше не является основной логикой продаж. Вместо него используется сценарный guided flow: пользователь выбирает варианты кнопками, backend хранит состояние сессии, на финальном шаге собирается лид и запускается уведомление владельцу.

## Endpoint'ы

### Старт / восстановление текущего шага

```http
POST /channels/web-chat/start
Content-Type: application/json
```

```json
{
  "session_id": "optional-existing-session-id",
  "reset": false
}
```

### Ответ на текущий шаг

```http
POST /channels/web-chat
Content-Type: application/json
```

```json
{
  "session_id": "session-id-from-start",
  "answer": "new_site"
}
```

`/channels/web-chat/message` оставлен как backward-compatible alias, но теперь он тоже работает по guided flow, а не как свободный AI-chat.

### Старый `/chat`

`POST /chat` отключён и возвращает `410 Gone`, чтобы случайно не использовать свободный чат вместо продажного сценария.

## Шаги сценария

1. Что нужно: новый сайт, редизайн, AI-форма, интеграции, не знаю.
2. Тип бизнеса.
3. Сроки.
4. Бюджет.
5. Канал контакта: Telegram, WhatsApp, Email.
6. Controlled input для самого контакта.
7. Финальный результат + CTA.

## Stateful storage

Сессии и ответы хранятся в таблицах:

- `conversation_sessions`
- `conversation_messages`

На финальном шаге создаётся запись в `leads`, если подключена БД лидов.

## Approval logic

Лид получает статус `qualified`, если:

- срок `urgent`, или
- бюджет `1500_plus`.

Иначе лид создаётся со статусом `new`.

## Frontend widget

Добавлен готовый виджет:

```html
<script src="https://YOUR_BACKEND_DOMAIN/static/web-chat-widget.js" data-api-base="https://YOUR_BACKEND_DOMAIN"></script>
```

Для локальной проверки:

```txt
http://127.0.0.1:8000/static/web-chat-demo.html
```

## Проверка curl

```bash
curl -X POST http://127.0.0.1:8000/channels/web-chat/start \
  -H "Content-Type: application/json" \
  -d '{}'
```

```bash
curl -X POST http://127.0.0.1:8000/channels/web-chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"PASTE_SESSION_ID","answer":"new_site"}'
```

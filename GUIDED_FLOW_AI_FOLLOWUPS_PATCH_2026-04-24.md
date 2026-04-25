# SiteFormo AI Sales Platform — guided flow + AI follow-ups patch

Дата: 2026-04-24

## Что изменено

Проект обновлён под новую продуктовую логику: вместо свободного чата используется guided sales flow / квиз-бот. Пользователь выбирает варианты кнопками, backend хранит state, собирает лид, создаёт предварительную оценку и формирует оффер.

## Основные изменения в коде

### 1. Guided web-chat вместо свободного `/chat`

Рабочие endpoints:

- `POST /channels/web-chat/start` — старт или восстановление сценария.
- `POST /channels/web-chat` — отправка выбранного варианта или контакта.
- `POST /channels/web-chat/message` — backward-compatible alias, теперь тоже работает через guided flow.

Свободный чат остаётся отключённым.

### 2. Stateful flow

Файл:

- `backend/app/services/guided_flow.py`

Шаги сценария:

1. Что нужно: новый сайт / редизайн / AI-форма / интеграции / не знаю.
2. Тип бизнеса.
3. Сроки запуска.
4. Бюджет.
5. Канал связи: Telegram / WhatsApp / Email.
6. Контакт: controlled input только в финальном шаге.
7. Done: результат, CTA, offer link, lead save.

### 3. Frontend widget с кнопками

Файл:

- `backend/app/static/web-chat-widget.js`

Изменения:

- нет свободного input во время сценария;
- input появляется только на шаге контакта;
- поддерживается `estimate`;
- поддерживается `offer` CTA;
- поддерживается основной CTA для WhatsApp / Telegram / SiteFormo;
- session id хранится в `localStorage`.

Подключение на сайте:

```html
<script src="https://YOUR_BACKEND_DOMAIN/static/web-chat-widget.js" data-api-base="https://YOUR_BACKEND_DOMAIN"></script>
```

Локальная демо-страница:

```txt
http://127.0.0.1:8000/static/web-chat-demo.html
```

### 4. Lead save + lead scoring

Файлы:

- `backend/app/services/db/models.py`
- `backend/app/services/db/migrations.py`
- `backend/alembic/versions/0005_guided_lead_nurturing.py`
- `backend/app/api/leads.py`

Добавлены поля лида:

- `contact_channel`
- `is_hot`
- `followup_stage`
- `last_contacted`
- `history`
- `estimate`
- `offer_url`

Hot lead logic:

- бюджет `€1500+`, или
- сроки `Срочно`.

Такие лиды получают статус `qualified`.

### 5. Offer / PDF proposal

Новый файл:

- `backend/app/services/offer_service.py`

Что делает:

- считает предварительную цену;
- считает ориентировочный срок;
- генерирует текст оффера;
- создаёт PDF через `reportlab`;
- если `reportlab` недоступен, не ломает flow и делает HTML-offer fallback.

Файлы офферов сохраняются в:

```txt
backend/app/static/offers
```

URL возвращается как:

```txt
/static/offers/<session_id>.pdf
```

### 6. AI-дожим лидов

Новый файл:

- `backend/app/services/lead_nurturing.py`

Логика:

- background worker запускается на startup FastAPI;
- каждые `GUIDED_FOLLOWUP_POLL_SECONDS` секунд проверяет лиды;
- отправляет/готовит follow-up по стадиям;
- если `OPENAI_API_KEY` есть, генерирует персонализированное сообщение;
- если OpenAI недоступен, использует fallback-шаблоны;
- история сообщений сохраняется в `leads.history`.

Стадии:

| Stage | Default delay |
|---|---:|
| 1 | 5 минут |
| 2 | 60 минут |
| 3 | 1440 минут |
| 4 | 4320 минут |

Важно: прямой Telegram-дожим пользователю работает только если контакт — numeric Telegram chat_id и пользователь уже писал боту. Для `@username` и WhatsApp без paid provider система отправляет владельцу AI-сгенерированное сообщение как подсказку для ручной отправки.

### 7. Environment variables

Добавлены во все env templates:

```env
ENABLE_GUIDED_FOLLOWUPS=true
GUIDED_FOLLOWUP_POLL_SECONDS=60
GUIDED_FOLLOWUP_STAGE_1_MINUTES=5
GUIDED_FOLLOWUP_STAGE_2_MINUTES=60
GUIDED_FOLLOWUP_STAGE_3_MINUTES=1440
GUIDED_FOLLOWUP_STAGE_4_MINUTES=4320
GUIDED_FOLLOWUP_MAX_STAGE=4
GUIDED_FOLLOWUP_SEND_TO_LEAD=false
OFFER_OUTPUT_DIR=app/static/offers
```

Для production также проверь:

```env
PUBLIC_BASE_URL=https://YOUR_RAILWAY_DOMAIN
DATABASE_URL=postgresql://...
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
TELEGRAM_BOT_TOKEN=...
OWNER_TELEGRAM_CHAT_ID=...
WHATSAPP_CONTACT_NUMBER=353...
TELEGRAM_BOT_USERNAME=your_bot_username
```

## Проверка, выполненная в sandbox

Проверены ключевые новые и изменённые файлы командой:

```bash
cd backend
python3 -S -m py_compile \
  app/services/guided_flow.py \
  app/services/offer_service.py \
  app/services/lead_nurturing.py \
  app/main.py \
  app/services/db/models.py \
  app/services/db/migrations.py \
  app/api/leads.py \
  alembic/versions/0005_guided_lead_nurturing.py
```

Результат: syntax check OK для ключевых изменённых файлов.

## Команды запуска локально

```bash
cd backend
cp .env.local.example .env.local
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Открыть демо:

```txt
http://127.0.0.1:8000/static/web-chat-demo.html
```

Проверить API:

```bash
curl -X POST http://127.0.0.1:8000/channels/web-chat/start \
  -H "Content-Type: application/json" \
  -d '{}'
```

## Команды Railway

В Railway variables добавь/проверь:

```env
APP_ENV=production
PUBLIC_BASE_URL=https://YOUR_RAILWAY_DOMAIN
DATABASE_URL=postgresql://YOUR_SUPABASE_POOLER_URL
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
TELEGRAM_BOT_TOKEN=...
OWNER_TELEGRAM_CHAT_ID=...
WHATSAPP_CONTACT_NUMBER=353...
ENABLE_GUIDED_FOLLOWUPS=true
```

Deploy command обычно остаётся стандартным для FastAPI/Railway, например:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

## Команды GitHub

```bash
git status
git add .
git commit -m "Implement guided AI sales flow and lead follow-ups"
git push origin main
```

## Что проверить после деплоя

1. `/health` возвращает `{"status":"ok"}`.
2. `/channels/web-chat/start` возвращает первый вопрос и options.
3. Виджет на сайте подключён не к старому `/chat`, а к `/channels/web-chat/start` и `/channels/web-chat`.
4. После финального контакта создаётся lead.
5. В Telegram владельцу приходит уведомление о новом лиде.
6. В ответе финального шага есть `estimate`, `offer`, `cta`.
7. Через заданные интервалы `followup_stage` увеличивается, а в `history` появляются AI/fallback сообщения.

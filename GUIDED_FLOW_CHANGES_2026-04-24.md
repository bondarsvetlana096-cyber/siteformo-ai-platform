# Guided flow changes — 2026-04-24

## Что изменено

- Свободный `POST /chat` отключён и возвращает `410 Gone`.
- Добавлен сценарный endpoint:
  - `POST /channels/web-chat/start`
  - `POST /channels/web-chat`
- Старый `POST /channels/web-chat/message` оставлен как legacy alias, но теперь использует guided flow.
- Добавлена state machine в `backend/app/services/guided_flow.py`.
- Добавлено хранение web-chat сессий и сообщений через `conversation_sessions` / `conversation_messages`.
- Финальный шаг создаёт лид в `leads`, если БД лидов подключена.
- Добавлена approval logic: `urgent` или `1500_plus` -> `qualified`, остальные -> `new`.
- Добавлен готовый frontend-виджет без свободного input:
  - `backend/app/static/web-chat-widget.js`
  - `backend/app/static/web-chat-demo.html`
- Input оставлен только на финальном controlled contact step.
- Добавлено подключение `/static` в FastAPI.
- Обновлена документация:
  - `README.md`
  - `backend/README.md`
  - `docs/GUIDED_WEB_CHAT_FLOW_RU.md`

## Проверка

Выполнена статическая проверка Python-кода:

```bash
cd backend
python3 -S -m py_compile $(find app -name '*.py')
```

Результат: `OK`.

Также выполнен AST parse всех файлов `backend/app/**/*.py`.

Результат: `syntax_errors 0`.

Полный runtime import через обычный `python3` в sandbox выполнить не удалось: системный Python в окружении зависает при загрузке site-packages без `-S`. Поэтому проверка выполнена на уровне синтаксиса/AST без импорта внешних зависимостей.

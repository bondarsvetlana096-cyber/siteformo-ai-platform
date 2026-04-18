# Vercel demo proxy

Этот мини-проект нужен для домена `demo-siteformo.com`.

## Что делает
- принимает запросы на `/demo/*`
- принимает запросы на `/demo-assets/*`
- проксирует их на `https://api.siteformo.com`

## Как использовать
1. Создай отдельный Vercel project из этой папки
2. Подключи домен `demo-siteformo.com`
3. Убедись, что в Railway:
   - `DEMO_BASE_URL=https://demo-siteformo.com`

## Важно
Если позже перейдёшь на `demo.siteformo.com`, просто поменяй `DEMO_BASE_URL` в Railway.

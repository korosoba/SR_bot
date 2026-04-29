# 🤖 SR_bot

> Telegram-бот для обработки новостных дайджестов о кино и сериалах через AI.

Часть системы из двух репозиториев:
- **[news-parser](https://github.com/korosoba/news-parser)** — парсит RSS-ленты и каждое утро автоматически присылает сводку в этот бот
- **SR_bot** (этот репозиторий) — получает сводку, обрабатывает через Groq и возвращает готовый дайджест

---

## Как это работает

```
[06:00 МСК] news-parser отправляет POST /process с текстом сводки
        ↓
SR_bot парсит статьи → отправляет в Groq батчами по 50
        ↓
Groq (Llama 4 Scout 17B) сортирует по категориям
        ↓
SR_bot возвращает digest-ДАТА.txt в Telegram
```

При ошибке Groq — автоматические повторные попытки:
- Первые 4 попытки каждые **15 минут**
- Затем каждые **60 минут** до 20:00 МСК

---

## HTTP API

Бот принимает запросы на порту `PORT` (задаётся Render автоматически).

### `GET /`
Health-check эндпоинт для keep-alive пингов. Возвращает `Bot is alive!`

### `POST /process`
Основной эндпоинт — запускает обработку дайджеста.
Вызывается автоматически из `daily-news.yml` репозитория news-parser.

**Тело запроса (JSON):**
```json
{
  "text": "содержимое news_feed.md",
  "date": "2026-04-29",
  "chat_id": "123456789"
}
```

**Ответ:** `200 OK` — обработка запущена в фоне, дайджест придёт в Telegram асинхронно.

---

## Что умеет Telegram-бот

**Отправь `.md` файл** → бот обработает его через Groq и вернёт `digest-ДАТА.txt`:

- 📋 Подборки — статьи формата "Лучшие X...", рейтинги, списки
- 🎬 Новые фильмы и сериалы — вышедшие в последние 1–3 года
- 🏛 Классика — вышедшие 10 и более лет назад
- 🌟 Персоны — статьи об актёрах, режиссёрах

**Отправь ссылку** → бот вернёт краткое резюме статьи на русском (5–7 предложений). При ошибке Groq — 6 попыток с паузой 10 секунд.

**Команда `/digest`** → бот попросит прислать `.md` файл.

---

## Файлы

| Файл | Назначение |
|---|---|
| `bot.py` | Основной код бота |
| `requirements.txt` | Python-зависимости |
| `runtime.txt` | Версия Python для Render |

---

## Деплой на Render

Бот работает как **web-сервис** на [Render](https://render.com) через long polling. Встроенный HTTP-сервер на порту `PORT` обслуживает keep-alive пинги (`GET /`) и входящие запросы на обработку дайджеста (`POST /process`).

Переменные окружения на Render:

| Переменная | Описание |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Токен бота от @BotFather |
| `GROQ_API_KEY` | API-ключ от [console.groq.com](https://console.groq.com) |
| `PORT` | Порт HTTP-сервера (Render подставляет автоматически) |

---

## Установка и локальный запуск

```bash
git clone https://github.com/korosoba/SR_bot
cd SR_bot
pip install -r requirements.txt

export TELEGRAM_BOT_TOKEN=ваш_токен
export GROQ_API_KEY=ваш_ключ

python bot.py
```

---

## Стек

- **Python 3.12**
- [python-telegram-bot](https://python-telegram-bot.org) — Telegram Bot API
- [trafilatura](https://trafilatura.readthedocs.io) — извлечение текста из статей по URL
- [Groq](https://console.groq.com) — Llama 4 Scout 17B для AI-обработки
- [Render](https://render.com) — хостинг

# 🤖 SR_bot

> Telegram-бот для обработки новостных дайджестов о кино и сериалах через AI.

Часть системы из двух репозиториев:
- **[news-parser](https://github.com/korosoba/news-parser)** — парсит RSS-ленты и каждое утро автоматически присылает сводку новостей в этот бот
- **SR_bot** (этот репозиторий) — получает сводку, обрабатывает её через Groq и возвращает готовый дайджест по категориям

---

## Как это работает

```
[06:00 МСК] news-parser присылает news-ДАТА.md
        ↓
SR_bot получает файл → парсит статьи → отправляет в Groq батчами
        ↓
Groq (Llama 4 Scout 17B) сортирует по категориям
        ↓
SR_bot возвращает digest-ДАТА.txt в Telegram
```

Если Groq недоступен — бот автоматически повторяет попытки:
- Первые 4 попытки каждые **15 минут**
- Затем каждые **60 минут** до 20:00 МСК

---

## Что умеет бот

**Отправь `.md` файл** → бот обработает его через Groq и вернёт `digest-ДАТА.txt` с категориями:

- 📋 Подборки — статьи формата "Лучшие X...", рейтинги, списки
- 🎬 Новые фильмы и сериалы — вышедшие в последние 1–3 года
- 🏛 Классика — вышедшие 10 и более лет назад
- 🌟 Персоны — статьи об актёрах, режиссёрах

**Отправь ссылку** → бот извлечёт текст статьи и вернёт краткое резюме на русском (5–7 предложений). При ошибке Groq — 6 попыток с паузой 10 секунд.

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

Бот работает как **worker-сервис** на [Render](https://render.com) через long polling. Встроенный health-сервер отвечает на `GET /` — используется для keep-alive пингов.

Переменные окружения задаются в настройках сервиса на Render:

| Переменная | Описание |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Токен бота от @BotFather |
| `GROQ_API_KEY` | API-ключ от [console.groq.com](https://console.groq.com) |
| `PORT` | Порт health-сервера (Render подставляет автоматически, дефолт 10000) |

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

"""
Новостной бот (SR_bot) — вынесен в отдельный модуль.
Запускается в отдельном потоке из app.py.
"""

import os
import json
import asyncio
import logging
import tempfile
import time
from datetime import datetime, timezone, timedelta

import requests
import trafilatura
from telegram import Update, Document
from telegram.ext import (
    ApplicationBuilder, MessageHandler, CommandHandler,
    filters, ContextTypes
)
from telegram.error import Conflict

logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["NEWS_BOT_TOKEN"]
MISTRAL_API_KEY = os.environ["MISTRAL_API_KEY"]

MISTRAL_MODEL = "mistral-small-latest"
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"

VK_TOKEN = os.getenv("VK_TOKEN", "")
VK_GROUP_ID = os.getenv("VK_GROUP_ID", "")  # числовой ID группы без минуса

BATCH_SIZE = 50
BATCH_PAUSE = 35

MSK = timezone(timedelta(hours=3))
DEADLINE_HOUR = 20
PHASE_1_INTERVAL = 15
PHASE_1_COUNT = 4
PHASE_2_INTERVAL = 60

_bot_loop: asyncio.AbstractEventLoop = None
_bot_app = None


def get_bot_loop():
    return _bot_loop


def mistral_request(messages: list, temperature: float = 0.3, max_tokens: int = 4000) -> str:
    response = requests.post(
        MISTRAL_URL,
        headers={
            "Authorization": f"Bearer {MISTRAL_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": MISTRAL_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def fetch_article(url: str):
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return None
    return trafilatura.extract(downloaded)


def process_with_mistral(article_text: str) -> str:
    prompt = f"""Ты — помощник, который обрабатывает англоязычные статьи.

Твоя задача:
1. Сделай краткое резюме статьи (5-7 предложений), выдели главные мысли
2. Переведи это резюме на русский язык

Отвечай ТОЛЬКО на русском языке. Формат ответа:

📌 Краткое резюме:
[текст резюме на русском]

Статья:
{article_text[:6000]}
"""
    return mistral_request(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5,
        max_tokens=1024,
    )


def parse_articles(md_text: str) -> list[dict]:
    articles = []
    blocks = md_text.split("---------")
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        if len(lines) < 3:
            continue
        title = lines[0].lstrip("# ").strip()
        tags = lines[1] if len(lines) > 1 else ""
        url = next((l for l in lines if l.startswith("http")), "")
        description = lines[-1] if not lines[-1].startswith("http") else ""
        articles.append({
            "title": title,
            "tags": tags,
            "url": url,
            "description": description,
        })
    return articles


DIGEST_PROMPT = """Ты — редактор, который сортирует статьи о кино и сериалах.

Вот список статей. Распредели каждую по категориям по правилам ниже.

ПРАВИЛА КАТЕГОРИЗАЦИИ:
- ПРОПУСТИТЬ (не включать): новости, анонсы, игры, техника, аниме, комиксы, статьи об индустрии (сборы, рейтинги, бизнес)
- 📋 ПОДБОРКИ: статьи формата "Лучшие X...", "10 лучших...", рейтинги, списки фильмов/сериалов
- 🎬 НОВЫЕ ФИЛЬМЫ И СЕРИАЛЫ: статьи о фильмах/сериалах вышедших примерно в последние 1-3 года (НЕ рецензии, НЕ подборки)
- 🏛 КЛАССИКА: статьи о фильмах/сериалах вышедших 10 и более лет назад
- 🌟 ПЕРСОНЫ: статьи о конкретных актёрах, режиссёрах, других интересных людях

ВАЖНО:
- Обработай ВСЕ статьи из списка, не пропускай ни одну подходящую
- Одна статья может попасть только в одну категорию
- Статьи о персонах (актёрах) включай в ПЕРСОНЫ, даже если они про старый фильм

ФОРМАТ ОТВЕТА:

📋 ПОДБОРКИ
• [Название статьи](ссылка)

🎬 НОВЫЕ ФИЛЬМЫ И СЕРИАЛЫ
• [Название статьи](ссылка)

🏛 КЛАССИКА
• [Название статьи](ссылка)

🌟 ПЕРСОНЫ
• [Название статьи](ссылка)

Если в категории нет статей — пропусти эту категорию совсем.
Названия статей НЕ переводи.

Вот статьи:

"""


def digest_batch_with_mistral(articles: list[dict]) -> str:
    articles_text = ""
    for i, a in enumerate(articles, 1):
        articles_text += f"{i}. {a['title']}\n   Теги: {a['tags']}\n   {a['description']}\n   {a['url']}\n\n"
    return mistral_request(
        messages=[{"role": "user", "content": DIGEST_PROMPT + articles_text}],
        temperature=0.3,
        max_tokens=4000,
    )


def merge_digests(batch_results: list[str]) -> str:
    categories = {
        "📋 ПОДБОРКИ": [],
        "🎬 НОВЫЕ ФИЛЬМЫ И СЕРИАЛЫ": [],
        "🏛 КЛАССИКА": [],
        "🌟 ПЕРСОНЫ": [],
    }
    current_cat = None
    for result in batch_results:
        for line in result.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line in categories:
                current_cat = line
            elif line.startswith("•") and current_cat:
                if line not in categories[current_cat]:
                    categories[current_cat].append(line)
    parts = []
    for cat, items in categories.items():
        if items:
            parts.append(cat)
            parts.extend(items)
            parts.append("")
    return "\n".join(parts).strip()


def digest_with_mistral(articles: list[dict]) -> tuple[str, int]:
    batches = [articles[i:i + BATCH_SIZE] for i in range(0, len(articles), BATCH_SIZE)]
    batch_results = []
    for i, batch in enumerate(batches):
        logger.info(f"Батч {i+1}/{len(batches)} ({len(batch)} статей)")
        result = digest_batch_with_mistral(batch)
        batch_results.append(result)
        if i < len(batches) - 1:
            logger.info(f"Пауза {BATCH_PAUSE} сек...")
            time.sleep(BATCH_PAUSE)
    return merge_digests(batch_results), len(batches)


def is_before_deadline() -> bool:
    return datetime.now(MSK).hour < DEADLINE_HOUR


def publish_to_vk(text: str, date_str: str) -> bool:
    """Публикует дайджест в закрытую VK-группу. Возвращает True если успешно."""
    if not VK_TOKEN or not VK_GROUP_ID:
        logger.info("VK не настроен, пропускаю публикацию")
        return False

    # VK принимает только текст без markdown-ссылок — конвертируем [текст](url) → текст: url
    import re
    vk_text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1: \2', text)
    vk_text = f"📰 Дайджест за {date_str}\n\n{vk_text}"

    # VK ограничивает пост 20 000 символов
    vk_text = vk_text[:20000]

    try:
        import urllib.request
        import urllib.parse
        url = "https://api.vk.com/method/wall.post"
        params = urllib.parse.urlencode({
            "owner_id": f"-{VK_GROUP_ID}",  # минус = группа
            "message": vk_text,
            "access_token": VK_TOKEN,
            "v": "5.199",
        }).encode()
        req = urllib.request.Request(url, data=params)
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            if "error" in result:
                logger.error(f"VK API error: {result['error']}")
                return False
            post_id = result.get("response", {}).get("post_id")
            logger.info(f"✅ VK: опубликован пост {post_id}")
            return True
    except Exception as e:
        logger.error(f"VK публикация не удалась: {e}")
        return False


async def process_digest_with_retry(bot, chat_id, articles, date_str, status_msg=None):
    n_batches = (len(articles) + BATCH_SIZE - 1) // BATCH_SIZE
    attempt = 0

    if status_msg is None:
        status_msg = await bot.send_message(
            chat_id=chat_id,
            text=f"🗞 Получена сводка за {date_str} ({len(articles)} статей). Начинаю обработку..."
        )

    while True:
        attempt += 1
        now_msk = datetime.now(MSK).strftime("%H:%M МСК")
        est_minutes = (n_batches * 35) // 60 + 1

        try:
            await status_msg.edit_text(
                f"🤖 Попытка #{attempt}: обрабатываю {len(articles)} статей "
                f"({n_batches} батчей, ~{est_minutes} мин)..."
            )
            result, _ = digest_with_mistral(articles)

            result_filename = f"digest-{date_str}.txt"
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as out:
                out.write(result)
                out_path = out.name

            await status_msg.delete()
            await bot.send_document(
                chat_id=chat_id,
                document=open(out_path, "rb"),
                filename=result_filename,
                caption=f"✅ Дайджест за {date_str} готов — {len(articles)} статей (попытка #{attempt})",
            )
            os.unlink(out_path)

            # Параллельно публикуем в VK (если настроено)
            vk_ok = publish_to_vk(result, date_str)
            if vk_ok:
                await bot.send_message(
                    chat_id=chat_id,
                    text="📌 Дайджест также опубликован в VK-группе"
                )
            return

        except Exception as e:
            logger.warning(f"Попытка #{attempt} не удалась: {e}")
            pause = PHASE_1_INTERVAL if attempt <= PHASE_1_COUNT else PHASE_2_INTERVAL
            next_try = datetime.now(MSK) + timedelta(minutes=pause)

            if not is_before_deadline() or next_try.hour >= DEADLINE_HOUR:
                await status_msg.edit_text(
                    f"❌ Mistral недоступен весь день. Дайджест за {date_str} не получен.\n"
                    f"Последняя попытка: {now_msk}\nОшибка: {str(e)[:200]}"
                )
                return

            await status_msg.edit_text(
                f"⚠️ Попытка #{attempt} не удалась ({now_msk})\nСледующая попытка через {pause} мин."
            )
            await asyncio.sleep(pause * 60)


async def process_digest_external(md_text: str, date_str: str, chat_id: int):
    articles = parse_articles(md_text)
    if not articles:
        await _bot_app.bot.send_message(
            chat_id=chat_id,
            text=f"❌ Не удалось найти статьи в файле за {date_str}."
        )
        return
    await process_digest_with_retry(
        bot=_bot_app.bot,
        chat_id=chat_id,
        articles=articles,
        date_str=date_str,
    )


async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    if isinstance(context.error, Conflict):
        logger.warning("Конфликт инстансов")
        return
    logger.error(f"Ошибка: {context.error}")


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not url.startswith("http"):
        await update.message.reply_text(
            "👋 Привет! Отправь ссылку на статью — сделаю краткое резюме на русском.\n"
            "Или отправь md-файл для обработки дайджеста."
        )
        return

    status_msg = await update.message.reply_text("⏳ Читаю статью...")
    article_text = fetch_article(url)
    if not article_text:
        await status_msg.edit_text("❌ Не удалось извлечь текст. Попробуй другую ссылку.")
        return

    await status_msg.edit_text("🤖 Обрабатываю через Mistral...")

    last_error = None
    for attempt in range(1, 7):
        try:
            result = process_with_mistral(article_text)
            await status_msg.edit_text(result)
            return
        except Exception as e:
            last_error = e
            if attempt < 6:
                await status_msg.edit_text(f"⏳ Попытка {attempt}/6 не удалась, повторяю через 10 сек...")
                await asyncio.sleep(10)

    await status_msg.edit_text(
        f"❌ Mistral недоступен — все 6 попыток не удались.\nОшибка: {str(last_error)[:200]}"
    )


async def handle_digest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📎 Отправь мне md-файл с дайджестом.")


async def handle_digest_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc: Document = update.message.document
    if not doc.file_name.endswith(".md"):
        await update.message.reply_text("❌ Нужен файл формата .md")
        return

    status_msg = await update.message.reply_text("⏳ Читаю файл...")
    tg_file = await context.bot.get_file(doc.file_id)

    with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tmp:
        await tg_file.download_to_drive(tmp.name)
        tmp_path = tmp.name

    with open(tmp_path, "r", encoding="utf-8") as f:
        md_text = f.read()
    os.unlink(tmp_path)

    articles = parse_articles(md_text)
    if not articles:
        await status_msg.edit_text("❌ Не удалось найти статьи в файле.")
        return

    date_str = doc.file_name.replace("news-", "").replace(".md", "")
    asyncio.create_task(
        process_digest_with_retry(
            bot=context.bot,
            chat_id=update.message.chat_id,
            articles=articles,
            date_str=date_str,
            status_msg=status_msg,
        )
    )


async def _run_polling():
    """Запускает polling вручную — без сигнальных хендлеров, безопасно в потоке."""
    async with _bot_app:
        await _bot_app.updater.start_polling(drop_pending_updates=False)
        await _bot_app.start()
        logger.info("SR_bot (news) polling запущен!")
        while True:
            await asyncio.sleep(3600)


def start_news_bot():
    """Запускается в отдельном потоке из app.py."""
    global _bot_loop, _bot_app

    _bot_app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    _bot_app.add_error_handler(handle_error)
    _bot_app.add_handler(CommandHandler("digest", handle_digest_command))
    _bot_app.add_handler(MessageHandler(filters.Document.MimeType("text/plain"), handle_digest_file))
    _bot_app.add_handler(MessageHandler(filters.Document.FileExtension("md"), handle_digest_file))
    _bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))

    _bot_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_bot_loop)

    logger.info("SR_bot thread: запускаю event loop...")
    _bot_loop.run_until_complete(_run_polling())

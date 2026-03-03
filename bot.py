import os
import logging
import threading
import tempfile
from http.server import HTTPServer, BaseHTTPRequestHandler
import trafilatura
from telegram import Update, Document
from telegram.ext import (
    ApplicationBuilder, MessageHandler, CommandHandler,
    filters, ContextTypes
)
from groq import Groq

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
PORT = int(os.environ.get("PORT", 10000))

groq_client = Groq(api_key=GROQ_API_KEY)


# --- Health server для Render ---

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass


def run_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()


# --- Парсинг и обработка статьи по ссылке ---

def fetch_article(url: str):
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return None
    return trafilatura.extract(downloaded)


def process_with_groq(article_text: str) -> str:
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
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5,
        max_tokens=1024,
    )
    return response.choices[0].message.content


# --- Обработка дайджеста ---

def parse_articles(md_text: str) -> list[dict]:
    """Парсит md-файл и возвращает список статей."""
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


def digest_with_groq(articles: list[dict]) -> str:
    """Отправляет список статей в Groq для категоризации."""
    # Формируем компактный список для Groq
    articles_text = ""
    for i, a in enumerate(articles, 1):
        articles_text += f"{i}. {a['title']}\n   Теги: {a['tags']}\n   {a['description']}\n   {a['url']}\n\n"

    prompt = f"""Ты — редактор, который сортирует статьи о кино и сериалах.

Вот список статей. Распредели каждую по категориям по правилам ниже.

ПРАВИЛА КАТЕГОРИЗАЦИИ:
- ПРОПУСТИТЬ (не включать): новости, анонсы, игры, техника, аниме, комиксы, статьи об индустрии (сборы, рейтинги, бизнес)
- 📋 ПОДБОРКИ: статьи формата "Лучшие X...", "10 лучших...", рейтинги, списки
- 🎬 НОВЫЕ ФИЛЬМЫ И СЕРИАЛЫ: статьи о фильмах/сериалах вышедших примерно в последние 1-3 года (НЕ рецензии)
- 🏛 КЛАССИКА: статьи о фильмах/сериалах вышедших 10 и более лет назад
- 🌟 ПЕРСОНЫ: статьи о конкретных актёрах, режиссёрах, интересных людях

ФОРМАТ ОТВЕТА — строго такой, без лишнего текста:

📋 ПОДБОРКИ
• [Название статьи]([ссылка])

🎬 НОВЫЕ ФИЛЬМЫ И СЕРИАЛЫ
• [Название статьи]([ссылка])

🏛 КЛАССИКА
• [Название статьи]([ссылка])

🌟 ПЕРСОНЫ
• [Название статьи]([ссылка])

Если в категории нет статей — всё равно напиши заголовок категории и напиши "нет статей".
Названия статей НЕ переводи — оставляй на английском.

Вот статьи:
{articles_text[:12000]}
"""

    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=3000,
    )
    return response.choices[0].message.content


# --- Handlers ---

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка ссылки на статью."""
    url = update.message.text.strip()

    if not url.startswith("http"):
        await update.message.reply_text(
            "👋 Привет! Отправь ссылку на статью — сделаю краткое резюме на русском.\n"
            "Или отправь /digest с md-файлом для обработки дайджеста."
        )
        return

    status_msg = await update.message.reply_text("⏳ Читаю статью...")
    article_text = fetch_article(url)

    if not article_text:
        await status_msg.edit_text("❌ Не удалось извлечь текст. Попробуй другую ссылку.")
        return

    await status_msg.edit_text("🤖 Обрабатываю через Groq...")

    try:
        result = process_with_groq(article_text)
    except Exception as e:
        logger.error(f"Groq error: {e}")
        await status_msg.edit_text("❌ Ошибка Groq. Попробуй чуть позже.")
        return

    await status_msg.edit_text(result)


async def handle_digest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /digest без файла — просим прислать файл."""
    await update.message.reply_text(
        "📎 Отправь мне md-файл с дайджестом (можно прямо с подписью /digest или просто файлом)."
    )


async def handle_digest_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка md-файла присланного как документ."""
    doc: Document = update.message.document

    # Принимаем только .md файлы
    if not doc.file_name.endswith(".md"):
        await update.message.reply_text("❌ Нужен файл формата .md")
        return

    status_msg = await update.message.reply_text("⏳ Читаю файл...")

    # Скачиваем файл во временную директорию
    tg_file = await context.bot.get_file(doc.file_id)
    with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tmp:
        await tg_file.download_to_drive(tmp.name)
        tmp_path = tmp.name

    with open(tmp_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    articles = parse_articles(md_text)
    if not articles:
        await status_msg.edit_text("❌ Не удалось найти статьи в файле.")
        return

    await status_msg.edit_text(f"🤖 Нашёл {len(articles)} статей, обрабатываю через Groq...")

    try:
        result = digest_with_groq(articles)
    except Exception as e:
        logger.error(f"Groq digest error: {e}")
        await status_msg.edit_text("❌ Ошибка Groq. Попробуй чуть позже.")
        return

    # Отправляем результат как файл
    date_str = doc.file_name.replace("news-", "").replace(".md", "")
    result_filename = f"digest-{date_str}.txt"

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as out:
        out.write(result)
        out_path = out.name

    await status_msg.delete()
    await update.message.reply_document(
        document=open(out_path, "rb"),
        filename=result_filename,
        caption=f"✅ Дайджест за {date_str} готов — {len(articles)} статей обработано",
    )

    os.unlink(tmp_path)
    os.unlink(out_path)


def main():
    # Запускаем health server
    thread = threading.Thread(target=run_health_server, daemon=True)
    thread.start()
    logger.info(f"Health server запущен на порту {PORT}")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # /digest без файла
    app.add_handler(CommandHandler("digest", handle_digest_command))

    # Файл .md — дайджест
    app.add_handler(MessageHandler(filters.Document.MimeType("text/plain"), handle_digest_file))
    app.add_handler(MessageHandler(filters.Document.FileExtension("md"), handle_digest_file))

    # Ссылка — резюме статьи
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))

    logger.info("Бот запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()

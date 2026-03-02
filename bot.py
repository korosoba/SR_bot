import os
import logging
import trafilatura
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from groq import Groq

# Логирование — чтобы видеть ошибки в Render
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Токены берём из переменных окружения (не из кода!)
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

groq_client = Groq(api_key=GROQ_API_KEY)


def fetch_article(url: str) -> str | None:
    """Скачивает и извлекает текст статьи по ссылке через trafilatura."""
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return None
    text = trafilatura.extract(downloaded)
    return text


def process_with_groq(article_text: str) -> str:
    """Отправляет текст в Groq и получает резюме + перевод на русском."""
    prompt = f"""Ты — помощник, который обрабатывает англоязычные статьи.

Твоя задача:
1. Сделай краткое резюме статьи (5-7 предложений), выдели главные мысли
2. Переведи это резюме на русский язык

Отвечай ТОЛЬКО на русском языке. Формат ответа:

📌 *Краткое резюме:*
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


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик входящих сообщений."""
    url = update.message.text.strip()

    # Простая проверка — похоже ли на ссылку
    if not url.startswith("http"):
        await update.message.reply_text(
            "👋 Привет! Отправь мне ссылку на статью (начинается с http), и я сделаю краткое резюме на русском."
        )
        return

    # Сообщаем что начали работу
    status_msg = await update.message.reply_text("⏳ Читаю статью...")

    # Парсим статью
    article_text = fetch_article(url)
    if not article_text:
        await status_msg.edit_text(
            "❌ Не удалось извлечь текст со страницы. Попробуй другую ссылку."
        )
        return

    await status_msg.edit_text("🤖 Обрабатываю через Groq...")

    # Отправляем в Groq
    try:
        result = process_with_groq(article_text)
    except Exception as e:
        logger.error(f"Groq error: {e}")
        await status_msg.edit_text(
            "❌ Ошибка при обращении к Groq. Попробуй чуть позже."
        )
        return

    # Отправляем результат
    await status_msg.edit_text(result, parse_mode="Markdown")


def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Бот запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()

"""
Единый Flask-сервер для двух ботов:
- SR_bot (новостной дайджест) — работает через polling в отдельном потоке
- CinemaPostBot (генерация постов) — webhook на /webhook/post

Endpoints:
  POST /process          — приём md-файла от GitHub Actions (для SR_bot)
  POST /webhook/post     — webhook CinemaPostBot
  GET  /health           — мониторинг (Better Uptime / cron-job.org)
"""

import os
import re
import json
import logging
import threading
import asyncio
import urllib.request
import urllib.parse

from flask import Flask, request, Response

# Импорты новостного бота
from news_bot import start_news_bot, parse_articles, send_digest

# Импорты пост-бота
from article_parser import parse_article
from post_generator import generate_post

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ── Переменные окружения пост-бота ────────────────────────────────────────────

POST_BOT_TOKEN = os.environ["POST_BOT_TOKEN"]
ALLOWED_USER_ID = int(os.environ["ALLOWED_USER_ID"])
POST_WEBHOOK_SECRET = os.getenv("POST_WEBHOOK_SECRET", "")
POST_API_BASE = f"https://api.telegram.org/bot{POST_BOT_TOKEN}"
VK_TOKEN = os.getenv("VK_TOKEN", "")
VK_GROUP_ID = os.getenv("VK_GROUP_ID", "")

# ── Helpers пост-бота ─────────────────────────────────────────────────────────

def tg_post(method: str, payload: dict, api_base: str):
    url = f"{api_base}/{method}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def send_message(chat_id: int, text: str):
    tg_post("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }, POST_API_BASE)


def send_post(chat_id: int, post_text: str, image_url, source_url: str):
    full_text = f"{post_text}\n\n🔗 <a href=\"{source_url}\">Источник</a>"
    caption = full_text[:1024]

    if image_url:
        try:
            # Скачиваем картинку с браузерным User-Agent
            req = urllib.request.Request(
                image_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            with urllib.request.urlopen(req, timeout=15) as img_resp:
                img_data = img_resp.read()
                content_type = img_resp.headers.get("Content-Type", "image/jpeg")

            logger.info(f"Картинка скачана: {len(img_data)} байт, тип: {content_type}")

            # Telegram принимает только JPEG/PNG/GIF/WEBP до 10MB
            if len(img_data) > 10 * 1024 * 1024:
                raise Exception(f"Картинка слишком большая: {len(img_data)} байт")

            # Определяем расширение по Content-Type
            if "png" in content_type:
                filename, filetype = "photo.png", "image/png"
            elif "gif" in content_type:
                filename, filetype = "photo.gif", "image/gif"
            elif "webp" in content_type:
                filename, filetype = "photo.webp", "image/webp"
            else:
                filename, filetype = "photo.jpg", "image/jpeg"

            boundary = "----TGPhotoBoundary"
            body = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="parse_mode"\r\n\r\nHTML\r\n'
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="photo"; filename="{filename}"\r\n'
                f"Content-Type: {filetype}\r\n\r\n"
            ).encode() + img_data + f"\r\n--{boundary}--\r\n".encode()

            url = f"{POST_API_BASE}/sendPhoto"
            req2 = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
            )
            with urllib.request.urlopen(req2, timeout=30) as resp:
                result = json.loads(resp.read())
                logger.info(f"✅ Фото отправлено в Telegram")

            if len(full_text) > 1024:
                send_message(chat_id, full_text[1024:])
            return

        except Exception as e:
            logger.warning(f"Фото не отправилось ({e}), отправляю текстом")

    send_message(chat_id, full_text[:4096])


def publish_to_vk(post_text: str, image_url, source_url: str) -> bool:
    """Публикует пост в VK-группу — текст + ссылка (VK сам подтянет превью)."""
    if not VK_TOKEN or not VK_GROUP_ID:
        logger.info("VK не настроен, пропускаю")
        return False

    clean_text = re.sub(r'<[^>]+>', '', post_text)
    vk_text = f"{clean_text}\n\n🔗 {source_url}"

    try:
        params = urllib.parse.urlencode({
            "owner_id": f"-{VK_GROUP_ID}",
            "message": vk_text[:4096],
            "access_token": VK_TOKEN,
            "v": "5.199",
        }).encode()
        req = urllib.request.Request(
            "https://api.vk.com/method/wall.post",
            data=params
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())

        if "error" in result:
            logger.error(f"VK wall.post error: {result['error']}")
            return False

        logger.info(f"✅ VK: опубликован пост {result.get('response', {}).get('post_id')}")
        return True

    except Exception as e:
        logger.error(f"VK публикация не удалась: {e}")
        return False


def extract_url(text: str):
    for word in text.split():
        if word.startswith("http://") or word.startswith("https://"):
            return word
    return None


# ── Webhook пост-бота ─────────────────────────────────────────────────────────

@app.route("/webhook/post", methods=["POST"])
def webhook_post():
    if POST_WEBHOOK_SECRET:
        token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if token != POST_WEBHOOK_SECRET:
            return Response("Forbidden", status=403)

    update = request.get_json(silent=True)
    if not update:
        return Response("OK", status=200)

    message = update.get("message") or update.get("edited_message")
    if not message:
        return Response("OK", status=200)

    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = message.get("text", "").strip()

    if user_id != ALLOWED_USER_ID:
        logger.warning(f"Ignored user_id={user_id}")
        return Response("OK", status=200)

    if not text:
        return Response("OK", status=200)

    url = extract_url(text)
    if not url:
        send_message(chat_id, "👋 Пришли мне ссылку на статью — сделаю пост для канала.")
        return Response("OK", status=200)

    send_message(chat_id, "⏳ Читаю статью...")

    try:
        article = parse_article(url)
        if not article:
            send_message(chat_id, "❌ Не удалось прочитать статью. Попробуй другую ссылку.")
            return Response("OK", status=200)

        send_message(chat_id, "✍️ Пишу пост...")
        post_text = generate_post(article)

        # Отправляем в Telegram
        send_post(chat_id, post_text, article.image_url, url)

        # Дублируем в VK
        try:
            vk_ok = publish_to_vk(post_text, article.image_url, url)
            if vk_ok:
                send_message(chat_id, "📌 Пост также опубликован в VK")
        except Exception as vk_err:
            logger.error(f"VK error: {vk_err}")

    except Exception as e:
        logger.error(f"Pipeline error: {e}")
        send_message(chat_id, f"❌ Ошибка: {e}")

    return Response("OK", status=200)


# ── Endpoint для GitHub Actions (SR_bot) ─────────────────────────────────────

@app.route("/process", methods=["POST"])
def process():
    try:
        data = request.get_json()
        md_text = data.get("text", "")
        date_str = data.get("date", "")
        chat_id = int(data.get("chat_id", 0))

        if not md_text or not date_str or not chat_id:
            return Response("Missing fields", status=400)

        logger.info(f"/process: дата={date_str}, chat_id={chat_id}")

        def run_digest():
            articles = parse_articles(md_text)
            if not articles:
                logger.error("Не удалось найти статьи в файле")
                return
            send_digest(articles, date_str, chat_id)

        threading.Thread(target=run_digest, daemon=True).start()
        return Response("OK", status=200)

    except Exception as e:
        logger.error(f"Ошибка в /process: {e}")
        return Response(str(e), status=500)


# ── Health ────────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return {"status": "ok"}, 200


@app.route("/")
def index():
    return {"status": "running", "bots": ["SR_bot", "CinemaPostBot"]}, 200


# ── Запуск новостного бота при старте модуля ─────────────────────────────────

news_thread = threading.Thread(target=start_news_bot, daemon=True)
news_thread.start()
logger.info("SR_bot запущен в отдельном потоке")


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

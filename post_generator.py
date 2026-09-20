"""
Generates a ready-to-publish Telegram post from article text.
Uses Gemini API via direct HTTP requests.
"""

import os
import json
import logging
import urllib.request

from article_parser import ParsedArticle

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GEMINI_MODEL = "gemini-3.1-flash-lite"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

SYSTEM_PROMPT = """Ты — редактор Telegram-канала о кино КороСоба.
Пишешь короткие, живые посты для аудитории, которая любит кино.

Получаешь текст статьи и должен написать готовый пост для Telegram.

Правила:
- Длина поста: 600–900 символов (это важно — не больше)
- Начни с яркого заголовка на русском с эмодзи (даже если статья на английском)
- 2–3 абзаца: суть новости, важный контекст, интересная деталь
- В конце — 3–5 хэштегов на русском (#кино #новости и т.д.)
- Стиль: живой, не сухой, как будто пишет человек, а не робот
- Если статья на английском — переведи суть, не переводи дословно
- Не добавляй ссылку — она будет прикреплена отдельно

Ответь ТОЛЬКО текстом поста, без каких-либо пояснений."""


def generate_post(article: ParsedArticle) -> str:
    """Generate a Telegram post from a parsed article via Gemini API."""
    user_prompt = f"""Статья: {article.url}

Заголовок: {article.title or '(нет)'}

Текст:
{article.text}"""

    full_prompt = f"{SYSTEM_PROMPT}\n\n{user_prompt}"

    payload = json.dumps({
        "contents": [{"parts": [{"text": full_prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 700},
    }).encode()

    url = f"{GEMINI_URL}?key={GEMINI_API_KEY}"
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
            post = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            logger.info(f"✅ Post generated: {len(post)} chars")
            return post
    except Exception as e:
        logger.error(f"Gemini request failed: {e}")
        raise

"""
Generates a ready-to-publish Telegram post from article text.
Uses Groq API via direct HTTP requests (no groq library — avoids httpx conflicts).
"""

import os
import json
import logging
import urllib.request

from article_parser import ParsedArticle

logger = logging.getLogger(__name__)

GROQ_API_KEY = os.environ["GROQ_API_KEY"]
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
HOT_THRESHOLD = int(os.getenv("HOT_THRESHOLD", "6"))

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
    """Generate a Telegram post from a parsed article via Groq HTTP API."""
    user_prompt = f"""Статья: {article.url}

Заголовок: {article.title or '(нет)'}

Текст:
{article.text}"""

    payload = json.dumps({
        "model": "llama-3.3-70b-versatile",
        "temperature": 0.7,
        "max_tokens": 700,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }).encode()

    req = urllib.request.Request(
        GROQ_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
            post = data["choices"][0]["message"]["content"].strip()
            logger.info(f"✅ Post generated: {len(post)} chars")
            return post
    except Exception as e:
        logger.error(f"Groq HTTP request failed: {e}")
        raise

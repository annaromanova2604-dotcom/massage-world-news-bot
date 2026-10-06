import asyncio
import logging
import os
import sqlite3
from datetime import datetime, time
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from openai import OpenAI
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-6-astra")
BOT_TIMEZONE = os.getenv("BOT_TIMEZONE", "America/Los_Angeles")
DIGEST_HOUR = int(os.getenv("DIGEST_HOUR", "9"))
DIGEST_MINUTE = int(os.getenv("DIGEST_MINUTE", "0"))
DB_PATH = os.getenv("DB_PATH", "bot.db")

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s",
                    level=logging.INFO)
logger = logging.getLogger("massage-news-bot")
client = OpenAI(api_key=OPENAI_API_KEY)

SYSTEM_PROMPT = """
Ты — редактор международного новостного дайджеста по индустрии массажа,
bodywork, wellness, реабилитации и массажных технологий.

Ищи и отбирай ДЕЙСТВИТЕЛЬНО АКТУАЛЬНЫЕ материалы по всему миру.

ТЕМЫ:
- ручной массаж, massage therapy, manual therapy, bodywork;
- myofascial therapy, trigger point, sports/therapeutic massage;
- lymphatic drainage и manual lymphatic drainage;
- аппаратный массаж и массажные устройства;
- vacuum massage, LPG/endermologie, RF/radiofrequency,
  ultrasound, EMS/electrostimulation, pressotherapy/compression,
  pneumatic massage, percussion therapy и похожие технологии;
- новые аппараты, производители, технологии и патенты;
- научные исследования, clinical trials, systematic reviews,
  meta-analyses, безопасность и эффективность;
- выставки, конгрессы и профессиональные мероприятия;
- рынок, бизнес, wellness-тренды;
- регулирование и стандарты, если они непосредственно затрагивают отрасль.

ПРАВИЛА:
- Ищи по всему миру, используя английские термины и при необходимости другие языки.
- Приоритет — публикациям последних 24–48 часов.
- Если важных материалов мало, расширь окно до 7 дней и укажи дату.
- Исключай SEO-мусор, обычную рекламу, дубли и старые материалы без нового факта.
- Не выдавай слухи за факты.
- Научные утверждения отделяй от маркетинговых.
- Для науки предпочитай первоисточники и рецензируемые публикации.
- Для новостей компаний по возможности используй официальный источник
  плюс независимое подтверждение.
- Не придумывай факты, даты, исследования или ссылки.
- Если материал не относится к массажу или непосредственно смежной технологии — исключай.

ОЦЕНКА:
9–10 очень важно; 7–8 важно; 5–6 интересно; ниже 5 не включать.

ФОРМАТ:
Сначала 3–5 главных новостей.
Затем:
🤲 РУЧНОЙ МАССАЖ
🤖 АППАРАТНЫЕ ТЕХНОЛОГИИ
🔬 НАУКА
💼 БИЗНЕС И РЫНОК
📅 СОБЫТИЯ И РЕГУЛИРОВАНИЕ

Для каждого материала: страна/флаг, заголовок, дата, категория,
оценка /10, 2–4 предложения по сути, строка "Почему важно:",
и ссылка на первоисточник, если она доступна.

В конце:
⭐ ТРЕНД ДНЯ — один наиболее заметный тренд и почему он важен.

Не используй Markdown-таблицы. Не делай длинных вступлений.
"""

SEARCH_PROMPT = """
ОБЯЗАТЕЛЬНО выполни веб-поиск. Ищи несколько независимых запросов:
massage therapy / manual massage / bodywork / manual therapy;
lymphatic drainage / myofascial;
massage devices / massage machines;
LPG / endermologie / vacuum massage;
RF massage / radiofrequency;
ultrasound / EMS / electrostimulation;
pressotherapy / compression / pneumatic massage;
clinical trial / systematic review / meta-analysis + massage;
massage industry / wellness technology / massage equipment;
conferences / exhibitions + massage.

Ищи свежие материалы за последние 48 часов; при нехватке — до 7 дней.
Отбрасывай дубли, рекламу и материалы без существенного нового факта.
"""

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS users(
        chat_id INTEGER PRIMARY KEY, created_at TEXT NOT NULL)""")
    conn.commit()
    conn.close()

def save_user(chat_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR IGNORE INTO users(chat_id, created_at) VALUES (?,?)",
                 (chat_id, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()

def get_users():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT chat_id FROM users").fetchall()
    conn.close()
    return [row[0] for row in rows]

def local_date():
    return datetime.now(ZoneInfo(BOT_TIMEZONE)).strftime("%Y-%m-%d")

async def build_digest():
    prompt = SEARCH_PROMPT + f"\nТекущая дата: {local_date()}.\n"
    response = await asyncio.to_thread(
        client.responses.create,
        model=OPENAI_MODEL,
        tools=[{"type": "web_search", "search_context_size": "high"}],
        tool_choice="required",
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )
    return response.output_text.strip()

async def send_long(bot, chat_id, text):
    while len(text) > 3900:
        cut = text.rfind("\n\n", 0, 3900)
        if cut < 1000:
            cut = text.rfind("\n", 0, 3900)
        if cut < 1000:
            cut = 3900
        await bot.send_message(chat_id=chat_id, text=text[:cut])
        text = text[cut:].lstrip()
    if text:
        await bot.send_message(chat_id=chat_id, text=text)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update.effective_chat.id)
    await update.message.reply_text(
        "Привет! Я Massage World News 🌍💆\n\n"
        "Команды:\n"
        "/news — свежие новости прямо сейчас\n"
        "/digest — полный дайджест\n"
        "/help — помощь\n\n"
        "Ежедневный дайджест будет приходить автоматически."
    )

async def help_cmd(update, context):
    await update.message.reply_text(
        "/news — найти свежие мировые новости\n"
        "/digest — полный аналитический дайджест\n"
        "/help — помощь"
    )

async def news(update, context):
    save_user(update.effective_chat.id)
    status = await update.message.reply_text("🔎 Ищу свежие мировые новости...")
    try:
        result = await build_digest()
        await status.delete()
        await send_long(context.bot, update.effective_chat.id, result)
    except Exception:
        logger.exception("Search failed")
        await status.edit_text(
            "Не удалось выполнить поиск. Проверь ключи API и настройки."
        )

async def digest(update, context):
    await news(update, context)

async def scheduled_digest(context):
    users = get_users()
    if not users:
        return
    try:
        result = await build_digest()
        for chat_id in users:
            try:
                await send_long(context.bot, chat_id, result)
            except Exception:
                logger.exception("Send failed for %s", chat_id)
    except Exception:
        logger.exception("Scheduled digest failed")

def main():
    init_db()
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("news", news))
    app.add_handler(CommandHandler("digest", digest))

    tz = ZoneInfo(BOT_TIMEZONE)
    app.job_queue.run_daily(
        scheduled_digest,
        time=time(DIGEST_HOUR, DIGEST_MINUTE, tzinfo=tz),
        name="daily_digest",
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

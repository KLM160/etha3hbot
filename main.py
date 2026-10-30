"""
⏳ يوماً ما | One Day — مساعد حامد الشخصي
--------------------------------------------------
main.py : نقطة التشغيل + القائمة الرئيسية + حارس المالك + إعداد أوامر البوت.

كل قسم ملف مستقل (todo.py, quran.py, english.py, qudrat.py, workout.py, radio.py)
ويحتوي: TITLE و MENU ودالة register(app).
لإضافة قسم جديد: أنشئ ملفه ثم أضفه إلى قائمة SECTIONS أدناه.
"""
import logging
import os
import random
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import BotCommand, BotCommandScopeChat, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import (
    ApplicationBuilder,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    TypeHandler,
)

from config import ALLOWED_USER_ID, OWNER_NAME, TOKEN
import english
import qudrat
import quran
import radio
import todo
import workout
from utils import grid, safe_edit_text

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger("bot")

# ترتيب الظهور في القائمة الرئيسية (صفّان لكل سطر)
SECTIONS = [todo, quran, english, qudrat, workout, radio]

MOTIVATION = [
    "Create your own effect. Every single day counts. ⏳",
    "No weakness here. Push forward towards KFUPM! 🥊",
    "Small daily victories lead to ultimate success. 💪",
    "Stay continuous, stay focused. One Day is built today. 🎯",
    "Work hard in silence, let your results make the noise. ✨",
    "Discipline today, freedom tomorrow. Keep building. 🔥",
    "You're not behind. You're building. Keep going. 🚀",
]


async def owner_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """🔒 يتجاهل أي تحديث من غير المالك بصمت قبل وصوله لأي Handler."""
    user = update.effective_user
    if user is None or user.id != ALLOWED_USER_ID:
        raise ApplicationHandlerStop


def home_text() -> str:
    return (
        "⏳ **يوماً ما | One Day**\n"
        "━━━━━━━ • ✨ • ━━━━━━━\n\n"
        f"يا هلا {OWNER_NAME} 👋\n"
        "يومك الجديد بدأ، وكل خطوة اليوم تقرّبك من حلمك. خلّنا نصنع أثرنا 💪\n\n"
        f"🔥 {random.choice(MOTIVATION)}\n\n"
        "👇 اختر القسم:"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    kb = InlineKeyboardMarkup(grid([InlineKeyboardButton(s.TITLE, callback_data=s.MENU) for s in SECTIONS]))
    if update.callback_query:
        await safe_edit_text(update.callback_query, home_text(), kb)
    else:
        await update.message.reply_text(home_text(), reply_markup=kb, parse_mode="Markdown")


async def main_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أزرار عامة: العودة للرئيسية + زر الترقيم الصامت (noop)."""
    query = update.callback_query
    await query.answer()
    if query.data == "main":
        await start(update, context)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("حدث استثناء أثناء معالجة تحديث: %s", context.error, exc_info=context.error)


async def post_init(app) -> None:
    """يضبط أوامر البوت ونصوص ملفه الشخصي في تيليجرام عند كل تشغيل."""
    try:
        await app.bot.delete_my_commands()  # يمسح الأوامر القديمة (ومنها /admin)
        await app.bot.set_my_commands(
            [BotCommand("start", "⏳ اللوحة الرئيسية")],
            scope=BotCommandScopeChat(chat_id=ALLOWED_USER_ID),
        )
        await app.bot.set_my_short_description("⏳ يوماً ما | One Day — مهامك وقرآنك ولغتك وقدراتك وتمرينك في مكان واحد.")
        await app.bot.set_my_description(
            "⏳ يوماً ما | One Day\n\n"
            "مساعد حامد الشخصي الخاص: المهام اليومية، القرآن الكريم، اللغة الإنجليزية، "
            "القدرات، التمرين، والإذاعة المدرسية في مكان واحد.\n\n"
            "Create your own effect. 💪\n"
            "🔒 البوت مغلق على صاحبه فقط."
        )
    except TelegramError as e:
        logger.warning("تعذّر ضبط أوامر/وصف البوت: %s", e)


class SimpleHandler(BaseHTTPRequestHandler):
    """خادم بسيط لإبقاء الخدمة حية على منصات مثل Render."""

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"One Day Bot Running")

    def log_message(self, format, *args):  # noqa: A002
        return


def run_dummy_server():
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 10000))), SimpleHandler).serve_forever()


def main():
    threading.Thread(target=run_dummy_server, daemon=True).start()
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    app.add_handler(TypeHandler(Update, owner_guard), group=-1)  # 🔒 حارس المالك
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(main_callbacks, pattern=r"^(main|noop)$"))

    for section in SECTIONS:
        section.register(app)

    app.add_error_handler(error_handler)
    logger.info("✅ One Day جاهز للعمل.")
    app.run_polling()


if __name__ == "__main__":
    main()

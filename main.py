"""
يوماً ما | One Day ⏳💪 — مساعد حامد الشخصي
--------------------------------------------------
main.py   : التشغيل + القائمة الرئيسية + حارس المالك + أوامر البوت.
config.py : الإعدادات (متغيرات البيئة).   utils.py : أدوات مشتركة + مزامنة GitHub.
كل قسم ملف مستقل (todo, quran, english, qudrat, workout, radio) ويحتوي: TITLE و MENU و register(app).
لإضافة قسم جديد: أنشئ ملفه ثم أضفه إلى SECTIONS أدناه.
"""
import asyncio
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
from utils import claim_panel, drop_old_panel, grid, safe_delete_msg, safe_edit_text, track_panel

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
# httpx يطبع روابط الطلبات وفيها توكن البوت — نخفيها من لوج Render
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("bot")

# ترتيب الظهور في القائمة الرئيسية
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
    """🔒 يتجاهل غير المالك بصمت، ويضمن واجهة واحدة فقط:
    أي زر من رسالة غير الواجهة الفعّالة (واجهة قديمة) تُحذف رسالته ويُوقَف التحديث."""
    user = update.effective_user
    if user is None or user.id != ALLOWED_USER_ID:
        raise ApplicationHandlerStop

    query = update.callback_query
    if query is not None and query.message is not None and not claim_panel(context, query.message):
        try:
            await query.answer("⚠️ هذه واجهة قديمة، استخدم الواجهة الحديثة أو أرسل /start", show_alert=True)
        except TelegramError:
            pass
        await safe_delete_msg(context, query.message.chat.id, query.message.message_id)
        raise ApplicationHandlerStop


def home_text() -> str:
    return (
        "**يوماً ما | One Day ⏳💪**\n"
        "━━━━━━━ • ✨ • ━━━━━━━\n\n"
        f"يا هلا {OWNER_NAME} 👋\n"
        "يومك الجديد بدأ، وكل خطوة اليوم تقرّبك من حلمك. خلّنا نصنع أثرنا 💪\n\n"
        f"🔥 {random.choice(MOTIVATION)}\n\n"
        "👇 اختر القسم:"
    )


def home_keyboard() -> InlineKeyboardMarkup:
    """الأول وحده، الأوسط بعمودين، والأخير وحده."""
    def btn(section) -> InlineKeyboardButton:
        return InlineKeyboardButton(section.TITLE, callback_data=section.MENU)

    rows = [[btn(SECTIONS[0])]]
    rows += grid([btn(s) for s in SECTIONS[1:-1]], cols=2)
    rows.append([btn(SECTIONS[-1])])
    return InlineKeyboardMarkup(rows)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شاشة تحكم واحدة فقط: المعرّف يُحفظ في bot_data لأن user_data تُمسح هنا."""
    context.user_data.clear()
    kb = home_keyboard()
    query = update.callback_query

    if query:  # رجوع للرئيسية: تعديل في نفس الرسالة
        await safe_edit_text(query, home_text(), kb)
        track_panel(context, query.message)
        return

    chat_id = update.effective_chat.id
    if update.effective_message:
        await safe_delete_msg(context, chat_id, update.effective_message.message_id)  # رسالة الأمر نفسها
    await drop_old_panel(context)  # الشاشة الفعّالة السابقة
    sent = await context.bot.send_message(chat_id, home_text(), reply_markup=kb, parse_mode="Markdown")
    track_panel(context, sent)


async def main_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أزرار عامة: العودة للرئيسية + زر الترقيم الصامت (noop)."""
    query = update.callback_query
    await query.answer()
    if query.data == "main":
        await start(update, context)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("حدث استثناء أثناء معالجة تحديث: %s", context.error, exc_info=context.error)


async def post_init(app) -> None:
    """يجلب أحدث أرشيف من GitHub، ثم يضبط أوامر البوت عند كل تشغيل."""
    await asyncio.to_thread(radio.restore_from_github)
    try:
        await app.bot.delete_my_commands()  # يمسح النطاق الافتراضي فقط فلا تظهر أوامر لغير المالك
        await app.bot.set_my_commands(
            [BotCommand("start", "⏳ اللوحة الرئيسية")],
            scope=BotCommandScopeChat(chat_id=ALLOWED_USER_ID),
        )
        await app.bot.set_my_short_description("")  # وصف فارغ حتى لا يظهر نص قبل /start
        await app.bot.set_my_description("")
    except TelegramError as e:
        logger.warning("تعذّر ضبط أوامر/وصف البوت: %s", e)


class SimpleHandler(BaseHTTPRequestHandler):
    """خادم بسيط لإبقاء الخدمة حية على Render (يدعم GET و HEAD لأدوات المراقبة)."""

    def _ok(self, body: bool):
        self.send_response(200)
        self.end_headers()
        if body:
            self.wfile.write(b"One Day Bot Running")

    def do_GET(self):
        self._ok(True)

    def do_HEAD(self):
        self._ok(False)

    def log_message(self, format, *args):  # noqa: A002
        return


def run_dummy_server():
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 10000))), SimpleHandler).serve_forever()


def main():
    threading.Thread(target=run_dummy_server, daemon=True).start()
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    app.add_handler(TypeHandler(Update, owner_guard), group=-1)  # 🔒 حارس المالك
    # group=1: محادثة الإضافة في radio.py تلتقط /start في group=0 لتُنهي نفسها، ثم تعمل start مستقلة هنا
    app.add_handler(CommandHandler("start", start), group=1)
    app.add_handler(CallbackQueryHandler(main_callbacks, pattern=r"^(main|noop)$"))

    for section in SECTIONS:
        section.register(app)

    app.add_error_handler(error_handler)
    logger.info("✅ One Day جاهز للعمل.")
    app.run_polling()


if __name__ == "__main__":
    main()

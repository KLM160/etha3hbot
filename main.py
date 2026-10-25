"""
🚀 Personal Dashboard | LifeOS — خاص بحامد
--------------------------------------------------
main.py : نقطة التشغيل + القائمة الرئيسية + حارس المالك.
كل قسم في ملف مستقل له دالة register(app):
  radio.py → 📻 الإذاعة المدرسية (الأرشيف السابق)
"""
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    TypeHandler,
)

import radio
from config import ALLOWED_USER_ID, OWNER_NAME, TOKEN
from utils import safe_edit_text

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger("bot")

# أقسام لوحة التحكم الشخصية (تُبنى تباعًا — كل قسم سيصبح ملفًا مستقلًا)
DASH_SECTIONS = {
    "dash_quran": "📖 القرآن وتطوير اللغة",
    "dash_study": "🎯 القدرات والدراسة",
    "dash_health": "🏋️ الصحة والرياضة",
    "dash_todo": "📝 المهام اليومية (To-Do)",
}


async def owner_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يتجاهل أي تحديث من غير المالك بصمت قبل وصوله لأي Handler."""
    user = update.effective_user
    if user is None or user.id != ALLOWED_USER_ID:
        raise ApplicationHandlerStop


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    rows = [[InlineKeyboardButton(title, callback_data=key)] for key, title in DASH_SECTIONS.items()]
    rows.append([InlineKeyboardButton("📻 الإذاعة المدرسية", callback_data="radio_menu")])
    text = f"🚀 **Personal Dashboard**\n━━━━━━━ • ✨ • ━━━━━━━\n\nأهلاً {OWNER_NAME} 👋\nاختر القسم:"
    if update.callback_query:
        await safe_edit_text(update.callback_query, text, InlineKeyboardMarkup(rows))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(rows), parse_mode="Markdown")


async def dashboard_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "main":
        return await start(update, context)

    title = DASH_SECTIONS.get(query.data)
    if title:
        back = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 الرئيسية", callback_data="main")]])
        await safe_edit_text(query, f"**{title}**\n━━━━━━━ • ✨ • ━━━━━━━\n\n🚧 هذا القسم قيد البناء.", back)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("حدث استثناء أثناء معالجة تحديث: %s", context.error, exc_info=context.error)


class SimpleHandler(BaseHTTPRequestHandler):
    """خادم بسيط لإبقاء الخدمة حية على منصات مثل Render."""

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Personal Dashboard Bot Running")

    def log_message(self, format, *args):  # noqa: A002
        return


def run_dummy_server():
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 10000))), SimpleHandler).serve_forever()


def main():
    threading.Thread(target=run_dummy_server, daemon=True).start()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(TypeHandler(Update, owner_guard), group=-1)  # 🔒 حارس المالك
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(dashboard_handler, pattern=r"^(main|dash_)"))

    radio.register(app)  # 📻 الإذاعة المدرسية

    app.add_error_handler(error_handler)
    logger.info("✅ Personal Dashboard جاهز للعمل.")
    app.run_polling()


if __name__ == "__main__":
    main()

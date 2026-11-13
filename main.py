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
import threading
from datetime import date, timedelta
from typing import Optional
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Update,
)
from telegram.error import TelegramError
from telegram.ext import (
    ApplicationBuilder,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
    TypeHandler,
)

from config import ALLOWED_USER_ID, HOME_IMAGE_ABOVE, HOME_IMAGE_URL, OWNER_NAME, TOKEN
import english
import qudrat
import quran
import radio
import todo
import workout
from utils import TZ, claim_panel, drop_old_panel, grid, restore_data_dir, safe_delete_msg, safe_edit_text, today, today_str, track_panel

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
# httpx يطبع روابط الطلبات وفيها توكن البوت — نخفيها من لوج Render
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("bot")

# ترتيب الظهور في القائمة الرئيسية
SECTIONS = [todo, quran, english, qudrat, workout, radio]


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


def home_preview() -> Optional[LinkPreviewOptions]:
    """معاينة الصورة في الرئيسية (None إن لم يُضبط HOME_IMAGE_URL فيعمل البوت بدون صورة)."""
    if not HOME_IMAGE_URL:
        return None
    return LinkPreviewOptions(
        url=HOME_IMAGE_URL,
        prefer_large_media=True,
        show_above_text=HOME_IMAGE_ABOVE,
    )


def live_status() -> str:
    """الشاشة الحية: تقييم أمس + مهام اليوم. أي خطأ في القراءة لا يكسر الرئيسية."""
    try:
        db = todo.load_archived()  # استيراد todo هنا آمن: todo لا يستورد main
        today_iso = today_str()

        # ⭐ تقييم أمس
        yesterday = today() - timedelta(days=1)
        yesterday_iso = yesterday.isoformat()
        if yesterday < todo.RATING_START:
            rating = f"⭐ **تقييم أمس:** {todo.before_start_sentence()}"
        elif yesterday_iso in db["ratings"]:
            score = db["ratings"][yesterday_iso]
            rating = (
                f"⭐ **تقييم أمس:** {score}/{todo.MAX_SCORE}\n"
                f"{todo.bar(score, todo.MAX_SCORE)} {todo.pct_of(score, todo.MAX_SCORE)}%"
            )
        else:
            rating = "⭐ **تقييم أمس:** لم يُقيَّم بعد"
        pending = [d for d in todo.pending_dates(db) if d != yesterday_iso]  # أمس معروض أعلاه
        if pending:
            rating += f"\n📌 أيام سابقة غير مقيَّمة: {todo.days_word(len(pending))}"

        # ✅ مهام اليوم (المنجز اليوم يشمل ما مُسح من المكتملة عبر done_stats)
        done = todo.done_in_range(db, today_iso, today_iso)
        left = sum(1 for t in db["tasks"] if not t["completed"])
        total = done + left
        if total:
            tasks = f"✅ **مهام اليوم:** {done} من {total}{' 🎉' if left == 0 else ''}\n{todo.bar(done, total)}"
        else:
            tasks = "✅ **مهام اليوم:** لا توجد مهام"
        return f"{rating}\n\n{tasks}"
    except Exception:  # noqa: BLE001
        logger.exception("تعذّر بناء الشاشة الحية")
        return ""


HIJRI_MONTHS = {
    1: "المحرم", 2: "صفر", 3: "ربيع الأول", 4: "ربيع الآخر", 5: "جمادى الأولى", 6: "جمادى الآخرة",
    7: "رجب", 8: "شعبان", 9: "رمضان", 10: "شوال", 11: "ذو القعدة", 12: "ذو الحجة",
}


def hijri_text(d: date) -> str:
    """التاريخ الهجري (أم القرى) بدون اسم اليوم، مثل: 2 جمادى الأولى 1448 هـ."""
    from hijri_converter import Gregorian  # استيراد متأخر: أي مشكلة فيه لا تكسر تشغيل البوت

    h = Gregorian(d.year, d.month, d.day).to_hijri()
    return f"{h.day} {HIJRI_MONTHS[h.month]} {h.year} هـ"


def home_text() -> str:
    d = today()
    dates = f"🗓️ {todo.fmt_date(d)}"
    try:
        dates = f"📅 {hijri_text(d)}\n{dates}"
    except Exception:  # noqa: BLE001
        logger.exception("تعذّر تحويل التاريخ الهجري")
    parts = [
        "**يوماً ما | One Day ⏳💪**\n━━━━━━━ • ✨ • ━━━━━━━",
        f"يا هلا {OWNER_NAME} 👋",
        dates,
        live_status(),
    ]
    return "\n\n".join(p for p in parts if p)


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
        await safe_edit_text(query, home_text(), kb, link_preview_options=home_preview())
        track_panel(context, query.message)
        return

    chat_id = update.effective_chat.id
    if update.effective_message:
        await safe_delete_msg(context, chat_id, update.effective_message.message_id)  # رسالة الأمر نفسها
    await drop_old_panel(context)  # الشاشة الفعّالة السابقة
    sent = await context.bot.send_message(
        chat_id, home_text(), reply_markup=kb, parse_mode="Markdown", link_preview_options=home_preview()
    )
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
    """يجلب أحدث أرشيف وملفات data من GitHub، ثم يضبط أوامر البوت عند كل تشغيل."""
    await asyncio.to_thread(radio.restore_from_github)
    await asyncio.to_thread(restore_data_dir)
    try:
        await app.bot.delete_my_commands()  # يمسح النطاق الافتراضي فقط فلا تظهر أوامر لغير المالك
        await app.bot.set_my_commands(
            [BotCommand("start", "⏳ اللوحة الرئيسية")],
            scope=BotCommandScopeChat(chat_id=ALLOWED_USER_ID),
        )
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
    app = ApplicationBuilder().token(TOKEN).defaults(Defaults(tzinfo=TZ)).post_init(post_init).build()

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
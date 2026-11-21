"""🎯 القدرات — لوحة الاختبار + حقيبة الملفات + الاختبارات التجريبية + عدّاد التدريب + بطاقة الاستعداد.

- البيانات في data/qudrat.json عبر load_json/save_json (مزامنة GitHub تلقائية).
- كل callback_data يبدأ بـ "qudrat_".
- لا يستورد هذا الملف main ولا todo ولا radio (لا استيرادات دائرية)؛ main يستورد منه exam_countdown_line فقط.
- محادثتان: إضافة محتوى، وتسجيل اختبار تجريبي؛ كلتاهما تنتهيان عند /start أو /cancel.
"""
import asyncio
import logging
import time
from datetime import date
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Update
from telegram.error import BadRequest, RetryAfter, TelegramError
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from utils import (
    build_paginated_keyboard,
    drop_old_panel,
    home_button,
    load_json,
    md_safe,
    safe_delete_msg,
    safe_edit_text,
    save_json,
    today,
    today_str,
    track_panel,
)

logger = logging.getLogger("bot.qudrat")

TITLE = "🎯 القدرات"
MENU = "qudrat_menu"
DB_FILE = "qudrat.json"
LINE = "━━━━━━━ • ✨ • ━━━━━━━"

# ----------------------------- بيانات الاختبار الثابتة -----------------------------
EXAM_TYPE = "اختبار القدرات العامة (التخصصات العلمية - محوسب)"
EXAM_DATE = date(2027, 1, 12)
EXAM_TIME = "08:15 صباحاً"
EXAM_VENUE = "مقر الاختبارات الرقمية - القصيم - بريدة (رجال)"
TARGET = 95

MAX_BATCH = 10      # أقصى عدد عناصر في دفعة الإضافة
SEND_DELAY = 0.3    # فاصل بين الملفات عند العرض (ثانية)
MAX_TITLE_LEN = 60

CATS = {"quant": "🔢 كمي", "verbal": "📖 لفظي", "general": "🗂️ شامل"}
FILTERS = {"all": "الكل", "quant": "كمي", "verbal": "لفظي", "general": "شامل"}

AR_DAYS = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
AR_MONTHS = [
    "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
    "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
]

NOPREVIEW = LinkPreviewOptions(is_disabled=True)

# حالات المحادثات
ADD_CAT, ADD_COLLECT, ADD_TITLE = range(3)
M_NAME, M_QUANT, M_VERBAL, M_TOTAL = range(10, 14)


# ----------------------------- تواريخ وعدّاد -----------------------------
def fmt_short(d: date) -> str:
    return f"{AR_DAYS[d.weekday()]} {d.day} {AR_MONTHS[d.month - 1]}"


def fmt_full(d: date) -> str:
    return f"{fmt_short(d)} {d.year}"


def days_left() -> int:
    return (EXAM_DATE - today()).days


def days_phrase(n: int) -> str:
    if n == 1:
        return "يوم واحد"
    if n == 2:
        return "يومان"
    if 3 <= n <= 10:
        return f"{n} أيام"
    return f"{n} يومًا"


def exam_countdown_line() -> str:
    """السطر الذي تعرضه الشاشة الحية في main.py (بتوقيت الرياض)."""
    d = days_left()
    if d > 0:
        return f"🎯 **اختبار القدرات:** متبقي {days_phrase(d)} ({fmt_short(EXAM_DATE)})"
    if d == 0:
        return "🔥 **اليوم اختبار القدرات! توكل على الله واستعن به 💪**"
    return "✅ **اختبار القدرات:** تم الاختبار"


def bar(value: float, maximum: float = 100, width: int = 10) -> str:
    filled = 0 if maximum <= 0 else round(max(0, min(value, maximum)) / maximum * width)
    return "▓" * filled + "░" * (width - filled)


def fmt_num(v) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:.1f}"


# ----------------------------- قاعدة البيانات -----------------------------
def load_db() -> dict:
    db = load_json(DB_FILE, {})
    if not isinstance(db, dict):
        db = {}
    db.setdefault("files", {})
    db.setdefault("mock_tests", [])
    stats = db.setdefault("study_stats", {})
    stats.setdefault("quant_questions", 0)
    stats.setdefault("verbal_questions", 0)
    db.setdefault("daily", {})
    return db


def new_id(db: dict, prefix: str = "i") -> str:
    base = int(time.time() * 1000)
    while f"{prefix}{base}" in db["files"]:
        base += 1
    return f"{prefix}{base}"


def short(title: str, limit: int = 38) -> str:
    return title if len(title) <= limit else title[: limit - 1] + "…"


# ----------------------------- مساعدات الواجهة -----------------------------
async def send_panel(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, kb: InlineKeyboardMarkup):
    """يرسل شاشة تحكم جديدة ويسجّلها كالشاشة الفعّالة."""
    try:
        msg = await context.bot.send_message(
            chat_id, text, reply_markup=kb, parse_mode="Markdown", link_preview_options=NOPREVIEW
        )
    except BadRequest as e:
        if "parse entities" not in str(e).lower():
            raise
        msg = await context.bot.send_message(chat_id, text, reply_markup=kb, link_preview_options=NOPREVIEW)
    track_panel(context, msg)
    return msg


async def edit_panel(context: ContextTypes.DEFAULT_TYPE, text: str, kb: Optional[InlineKeyboardMarkup]) -> None:
    """يعدّل الشاشة الفعّالة من داخل معالج رسالة (لا يوجد query)."""
    ref = context.user_data.get("q_panel")
    if not ref:
        return
    try:
        await context.bot.edit_message_text(
            text, chat_id=ref[0], message_id=ref[1], reply_markup=kb,
            parse_mode="Markdown", link_preview_options=NOPREVIEW,
        )
    except BadRequest as e:
        err = str(e).lower()
        if "not modified" in err:
            return
        if "parse entities" in err:
            try:
                await context.bot.edit_message_text(
                    text, chat_id=ref[0], message_id=ref[1], reply_markup=kb, link_preview_options=NOPREVIEW
                )
            except TelegramError:
                pass
            return
        logger.warning("تعذّر تعديل الشاشة: %s", e)
    except TelegramError as e:
        logger.warning("تعذّر تعديل الشاشة: %s", e)


def clear_temp(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in ("q_panel", "q_cat", "q_items", "q_mock"):
        context.user_data.pop(key, None)


def nav_row() -> list:
    return [InlineKeyboardButton(TITLE, callback_data=MENU), home_button()]


# ----------------------------- الشاشة الرئيسية للقسم -----------------------------
def menu_text(db: dict) -> str:
    d = days_left()
    if d > 0:
        countdown = f"⏳ **المتبقي:** {days_phrase(d)}"
    elif d == 0:
        countdown = "🔥 **اليوم هو الاختبار! توكل على الله 💪**"
    else:
        countdown = "✅ **تم الاختبار**"

    mocks = db["mock_tests"]
    last = f"{fmt_num(mocks[-1]['total'])}/100" if mocks else "لا يوجد بعد"
    stats = db["study_stats"]
    q, v = stats["quant_questions"], stats["verbal_questions"]

    return (
        f"**{TITLE}**\n{LINE}\n\n"
        f"📌 {EXAM_TYPE}\n"
        f"🗓️ {fmt_full(EXAM_DATE)} — {EXAM_TIME}\n"
        f"📍 {EXAM_VENUE}\n"
        f"🎯 **الهدف:** {TARGET}+ / 100\n"
        f"{countdown}\n\n"
        f"📊 **ملخص سريع**\n"
        f"📁 الملفات المحفوظة: {len(db['files'])}\n"
        f"📝 آخر تجريبي: {last}\n"
        f"✏️ الأسئلة المحلولة: {q + v} (🔢 {q} • 📖 {v})"
    )


def menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ إضافة محتوى", callback_data="qudrat_add"),
            InlineKeyboardButton("📚 تصفح الملفات", callback_data="qudrat_browse:all:0"),
        ],
        [
            InlineKeyboardButton("📝 الاختبارات التجريبية", callback_data="qudrat_mocks"),
            InlineKeyboardButton("⚡ عدّاد التدريب", callback_data="qudrat_train"),
        ],
        [
            InlineKeyboardButton("🎒 تذكير ليلة الاختبار", callback_data="qudrat_prep"),
            InlineKeyboardButton("🗑️ إدارة وحذف", callback_data="qudrat_manage:0"),
        ],
        [home_button()],
    ])


async def menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await safe_edit_text(q, menu_text(load_db()), menu_kb())
    track_panel(context, q.message)


async def stale_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """زر من عملية انتهت (مثلاً بعد إعادة تشغيل البوت): نرجع لقائمة القسم."""
    q = update.callback_query
    await q.answer("انتهت هذه العملية، ابدأ من جديد 🔄")
    await safe_edit_text(q, menu_text(load_db()), menu_kb())
    track_panel(context, q.message)


# ----------------------------- التصفح والفلترة -----------------------------
def browse_view(db: dict, cat: str, page: int):
    items = [(i, it) for i, it in db["files"].items() if cat == "all" or it.get("category") == cat]
    items.reverse()  # الأحدث أولاً
    buttons = [
        InlineKeyboardButton(
            f"{CATS.get(it.get('category'), '🗂️')} {short(it.get('title', 'بدون عنوان'))} ({len(it.get('files', []))})",
            callback_data=f"qudrat_open:{i}",
        )
        for i, it in items
    ]
    kb = build_paginated_keyboard(buttons, page, f"qudrat_browse:{cat}", extra_rows=[nav_row()])
    filter_row = [
        InlineKeyboardButton(("✅ " if key == cat else "") + label, callback_data=f"qudrat_browse:{key}:0")
        for key, label in FILTERS.items()
    ]
    kb = InlineKeyboardMarkup([filter_row] + [list(r) for r in kb.inline_keyboard])
    if items:
        text = f"**📚 الملفات — {FILTERS[cat]}**\n{LINE}\n\nاختر حزمة لعرض محتواها:"
    else:
        text = f"**📚 الملفات — {FILTERS[cat]}**\n{LINE}\n\n📭 لا توجد حزم في هذا التصنيف بعد."
    return text, kb


async def browse_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, cat, page = q.data.split(":")
    text, kb = browse_view(load_db(), cat, int(page))
    await safe_edit_text(q, text, kb)
    track_panel(context, q.message)


async def send_entry(context: ContextTypes.DEFAULT_TYPE, chat_id: int, entry: dict) -> bool:
    kind, fid, caption = entry.get("type"), entry.get("file_id"), entry.get("caption")
    senders = {
        "document": context.bot.send_document,
        "photo": context.bot.send_photo,
        "video": context.bot.send_video,
        "audio": context.bot.send_audio,
        "voice": context.bot.send_voice,
    }
    for attempt in range(2):
        try:
            if kind == "text":
                await context.bot.send_message(chat_id, entry.get("text") or "")
            elif kind in senders:
                await senders[kind](chat_id, fid, caption=caption)
            else:
                return False
            return True
        except RetryAfter as e:
            wait = e.retry_after.total_seconds() if hasattr(e.retry_after, "total_seconds") else e.retry_after
            await asyncio.sleep(float(wait) + 1)
        except TelegramError as e:
            logger.warning("تعذّر إرسال عنصر (%s): %s", kind, e)
            return False
    return False


async def open_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    item_id = q.data.split(":", 1)[1]
    db = load_db()
    item = db["files"].get(item_id)
    if not item:
        await q.answer("⚠️ هذه الحزمة غير موجودة", show_alert=True)
        text, kb = browse_view(db, "all", 0)
        await safe_edit_text(q, text, kb)
        return
    await q.answer()

    chat_id = q.message.chat.id
    title = item.get("title", "بدون عنوان")
    await safe_edit_text(q, f"⏳ جارٍ تحميل **{md_safe(title)}** ...", None)

    await context.bot.send_message(chat_id, f"📂 {title}\n{CATS.get(item.get('category'), '')}")
    failed = 0
    for entry in item.get("files", []):
        if not await send_entry(context, chat_id, entry):
            failed += 1
        await asyncio.sleep(SEND_DELAY)

    await drop_old_panel(context)  # شاشة التحميل
    footer = f"✅ انتهى عرض **{md_safe(title)}**"
    if failed:
        footer += f"\n⚠️ تعذّر إرسال {failed} عنصر"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📚 رجوع للملفات", callback_data="qudrat_browse:all:0")],
        nav_row(),
    ])
    await send_panel(context, chat_id, footer, kb)


# ----------------------------- الإدارة والحذف -----------------------------
def manage_view(db: dict, page: int):
    items = list(db["files"].items())
    items.reverse()
    buttons = [
        InlineKeyboardButton(
            f"🗑️ {short(it.get('title', 'بدون عنوان'))} ({len(it.get('files', []))})",
            callback_data=f"qudrat_del:{i}",
        )
        for i, it in items
    ]
    kb = build_paginated_keyboard(buttons, page, "qudrat_manage", extra_rows=[nav_row()])
    if items:
        text = f"**🗑️ إدارة وحذف**\n{LINE}\n\nاضغط على الحزمة التي تريد حذفها:"
    else:
        text = f"**🗑️ إدارة وحذف**\n{LINE}\n\n📭 لا توجد حزم محفوظة."
    return text, kb


async def manage_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    text, kb = manage_view(load_db(), int(q.data.split(":")[1]))
    await safe_edit_text(q, text, kb)
    track_panel(context, q.message)


async def delete_ask_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    item_id = q.data.split(":", 1)[1]
    item = load_db()["files"].get(item_id)
    if not item:
        await q.answer("⚠️ غير موجودة", show_alert=True)
        text, kb = manage_view(load_db(), 0)
        await safe_edit_text(q, text, kb)
        return
    await q.answer()
    text = (
        f"⚠️ **تأكيد الحذف**\n{LINE}\n\n"
        f"هل تريد حذف «{md_safe(item.get('title', ''))}» "
        f"({len(item.get('files', []))} عنصر)؟\nلا يمكن التراجع بعد الحذف."
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ نعم، احذف", callback_data=f"qudrat_delyes:{item_id}"),
        InlineKeyboardButton("❌ تراجع", callback_data="qudrat_manage:0"),
    ]])
    await safe_edit_text(q, text, kb)


async def delete_yes_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    item_id = q.data.split(":", 1)[1]
    db = load_db()
    if db["files"].pop(item_id, None) is not None:
        save_json(DB_FILE, db)
        await q.answer("🗑️ تم الحذف")
    else:
        await q.answer("⚠️ غير موجودة", show_alert=True)
    text, kb = manage_view(db, 0)
    await safe_edit_text(q, text, kb)
    track_panel(context, q.message)


# ----------------------------- محادثة: إضافة محتوى -----------------------------
def extract_entry(msg) -> Optional[dict]:
    caption = msg.caption
    if msg.document:
        return {"file_id": msg.document.file_id, "type": "document", "text": None, "caption": caption}
    if msg.photo:
        return {"file_id": msg.photo[-1].file_id, "type": "photo", "text": None, "caption": caption}
    if msg.video:
        return {"file_id": msg.video.file_id, "type": "video", "text": None, "caption": caption}
    if msg.audio:
        return {"file_id": msg.audio.file_id, "type": "audio", "text": None, "caption": caption}
    if msg.voice:
        return {"file_id": msg.voice.file_id, "type": "voice", "text": None, "caption": caption}
    if msg.text:
        return {"file_id": None, "type": "text", "text": msg.text, "caption": None}
    return None


def cancel_kb(data: str) -> list:
    return [InlineKeyboardButton("❌ إلغاء", callback_data=data)]


async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    clear_temp(context)
    context.user_data["q_panel"] = (q.message.chat.id, q.message.message_id)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f"qudrat_addcat:{key}") for key, label in CATS.items()],
        cancel_kb("qudrat_addcancel"),
    ])
    await safe_edit_text(q, f"**➕ إضافة محتوى**\n{LINE}\n\nاختر التصنيف أولاً:", kb)
    track_panel(context, q.message)
    return ADD_CAT


def collect_view(cat: str, count: int, warn: str = ""):
    text = (
        f"**➕ إضافة محتوى — {CATS[cat]}**\n{LINE}\n\n"
        f"أرسل المواد: PDF، صور، فيديو، صوت/فويس، أو نص.\n"
        f"📥 تم استقبال: **{count}/{MAX_BATCH}**"
    )
    if warn:
        text += f"\n\n{warn}"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ اكتفيت، انتقل للعنوان", callback_data="qudrat_adddone")],
        cancel_kb("qudrat_addcancel"),
    ])
    return text, kb


async def add_cat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    cat = q.data.split(":")[1]
    if cat not in CATS:
        return ADD_CAT
    context.user_data["q_cat"] = cat
    context.user_data["q_items"] = []
    text, kb = collect_view(cat, 0)
    await safe_edit_text(q, text, kb)
    return ADD_COLLECT


async def add_collect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.effective_message
    cat = context.user_data.get("q_cat", "general")
    items = context.user_data.setdefault("q_items", [])

    if len(items) >= MAX_BATCH:
        text, kb = collect_view(cat, len(items), f"⚠️ وصلت للحد الأقصى ({MAX_BATCH})؛ الرسالة الأخيرة لم تُحفظ. اضغط «اكتفيت».")
        await edit_panel(context, text, kb)
        return ADD_COLLECT

    entry = extract_entry(msg)
    if entry is None:
        return ADD_COLLECT
    items.append(entry)
    await safe_delete_msg(context, msg.chat.id, msg.message_id)
    text, kb = collect_view(cat, len(items))
    await edit_panel(context, text, kb)
    return ADD_COLLECT


async def add_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    items = context.user_data.get("q_items", [])
    if not items:
        await q.answer("أرسل مادة واحدة على الأقل أولاً", show_alert=True)
        return ADD_COLLECT
    await q.answer()
    kb = InlineKeyboardMarkup([cancel_kb("qudrat_addcancel")])
    await safe_edit_text(q, f"**✏️ عنوان الحزمة**\n{LINE}\n\n📦 {len(items)} عنصر.\nأرسل الآن اسم/عنوان الحزمة:", kb)
    return ADD_TITLE


async def add_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.effective_message
    title = (msg.text or "").strip()[:MAX_TITLE_LEN]
    if not title:
        return ADD_TITLE

    items = context.user_data.get("q_items", [])
    cat = context.user_data.get("q_cat", "general")
    if not items:  # حالة نادرة: ضاعت الدفعة
        await safe_delete_msg(context, msg.chat.id, msg.message_id)
        clear_temp(context)
        await drop_old_panel(context)
        await send_panel(context, msg.chat.id, menu_text(load_db()), menu_kb())
        return ConversationHandler.END

    db = load_db()
    db["files"][new_id(db)] = {
        "title": title,
        "category": cat,
        "files": items,
        "created": today_str(),
    }
    save_json(DB_FILE, db)

    await safe_delete_msg(context, msg.chat.id, msg.message_id)
    clear_temp(context)
    await drop_old_panel(context)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إضافة أخرى", callback_data="qudrat_add")],
        nav_row(),
    ])
    await send_panel(
        context, msg.chat.id,
        f"✅ **تم الحفظ**\n{LINE}\n\n📦 {md_safe(title)}\n{CATS[cat]} • {len(items)} عنصر",
        kb,
    )
    return ConversationHandler.END


async def add_cancel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer("تم الإلغاء")
    clear_temp(context)
    await safe_edit_text(q, menu_text(load_db()), menu_kb())
    track_panel(context, q.message)
    return ConversationHandler.END


async def start_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/start داخل المحادثة: ننهيها فقط، ثم يعمل start الرئيسي (group=1) فيبني الواجهة."""
    clear_temp(context)
    return ConversationHandler.END


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/cancel داخل المحادثة: يلغي العملية ويعيد قائمة القسم كشاشة وحيدة."""
    msg = update.effective_message
    clear_temp(context)
    if msg:
        await safe_delete_msg(context, msg.chat.id, msg.message_id)
    await drop_old_panel(context)
    await send_panel(context, update.effective_chat.id, menu_text(load_db()), menu_kb())
    return ConversationHandler.END


# ----------------------------- الاختبارات التجريبية -----------------------------
def mocks_view(db: dict):
    mocks = db["mock_tests"]
    lines = [f"**📝 الاختبارات التجريبية**\n{LINE}\n"]
    if not mocks:
        lines.append("📭 لم تسجّل أي اختبار تجريبي بعد.")
    else:
        n = len(mocks)
        avg_total = sum(m["total"] for m in mocks) / n
        avg_q = sum(m["quant"] for m in mocks) / n
        avg_v = sum(m["verbal"] for m in mocks) / n
        gap = TARGET - avg_total
        goal = "✅ فوق الهدف" if gap <= 0 else f"تحتاج +{gap:.1f} للوصول للهدف"
        lines.append(
            f"📊 **متوسط الأداء:** {avg_total:.1f}/100 ({n} نموذج)\n"
            f"{bar(avg_total)} {round(avg_total)}%\n"
            f"🔢 كمي: {avg_q:.1f} • 📖 لفظي: {avg_v:.1f}\n"
            f"🎯 الهدف {TARGET}+ — {goal}\n"
        )
        for m in reversed(mocks[-8:]):
            lines.append(
                f"• {m.get('date', '')} — **{md_safe(m.get('title', ''))}**\n"
                f"   🔢 {fmt_num(m['quant'])} | 📖 {fmt_num(m['verbal'])} | 🎯 {fmt_num(m['total'])}"
            )
        if n > 8:
            lines.append(f"\n… وعدد {n - 8} أقدم غير معروض")
    rows = [[InlineKeyboardButton("➕ تسجيل اختبار تجريبي", callback_data="qudrat_mockadd")]]
    if mocks:
        rows.append([InlineKeyboardButton("🗑️ حذف آخر نموذج", callback_data="qudrat_mockdel")])
    rows.append(nav_row())
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def mocks_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    text, kb = mocks_view(load_db())
    await safe_edit_text(q, text, kb)
    track_panel(context, q.message)


async def mock_del_ask_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    mocks = load_db()["mock_tests"]
    if not mocks:
        await q.answer("لا توجد نماذج", show_alert=True)
        return
    await q.answer()
    m = mocks[-1]
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ نعم، احذف", callback_data="qudrat_mockdelyes"),
        InlineKeyboardButton("❌ تراجع", callback_data="qudrat_mocks"),
    ]])
    await safe_edit_text(q, f"⚠️ **تأكيد الحذف**\n{LINE}\n\nحذف آخر نموذج: «{md_safe(m.get('title', ''))}» ({fmt_num(m['total'])}/100)؟", kb)


async def mock_del_yes_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    db = load_db()
    if db["mock_tests"]:
        db["mock_tests"].pop()
        save_json(DB_FILE, db)
        await q.answer("🗑️ تم الحذف")
    else:
        await q.answer()
    text, kb = mocks_view(db)
    await safe_edit_text(q, text, kb)
    track_panel(context, q.message)


def parse_score(text: str) -> Optional[float]:
    table = str.maketrans("٠١٢٣٤٥٦٧٨٩٫،,", "0123456789...")
    try:
        v = float((text or "").strip().translate(table))
    except ValueError:
        return None
    if not 0 <= v <= 100:
        return None
    return int(v) if v.is_integer() else round(v, 1)


def mock_prompt(step: str, warn: str = ""):
    prompts = {
        "name": "أرسل اسم النموذج (مثال: نموذج محوسب 1):",
        "quant": "أرسل درجة **الكمي** (0 - 100):",
        "verbal": "أرسل درجة **اللفظي** (0 - 100):",
        "total": "أرسل **الدرجة الكلية** (0 - 100)، أو اضغط الحساب التلقائي:",
    }
    text = f"**📝 تسجيل اختبار تجريبي**\n{LINE}\n\n{prompts[step]}"
    if warn:
        text += f"\n\n{warn}"
    rows = []
    if step == "total":
        rows.append([InlineKeyboardButton("🧮 احسبها تلقائيًا (المتوسط)", callback_data="qudrat_mocktotalauto")])
    rows.append(cancel_kb("qudrat_mockcancel"))
    return text, InlineKeyboardMarkup(rows)


async def mock_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    clear_temp(context)
    context.user_data["q_panel"] = (q.message.chat.id, q.message.message_id)
    context.user_data["q_mock"] = {}
    text, kb = mock_prompt("name")
    await safe_edit_text(q, text, kb)
    track_panel(context, q.message)
    return M_NAME


async def mock_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.effective_message
    name = (msg.text or "").strip()[:MAX_TITLE_LEN]
    await safe_delete_msg(context, msg.chat.id, msg.message_id)
    if not name:
        return M_NAME
    context.user_data.setdefault("q_mock", {})["title"] = name
    text, kb = mock_prompt("quant")
    await edit_panel(context, text, kb)
    return M_QUANT


async def _mock_score_step(update, context, key: str, this_state: int, next_state: int, next_step: str) -> int:
    msg = update.effective_message
    score = parse_score(msg.text)
    await safe_delete_msg(context, msg.chat.id, msg.message_id)
    if score is None:
        step = {"quant": "quant", "verbal": "verbal", "total": "total"}[key]
        text, kb = mock_prompt(step, "⚠️ أرسل رقمًا صحيحًا من 0 إلى 100.")
        await edit_panel(context, text, kb)
        return this_state
    context.user_data.setdefault("q_mock", {})[key] = score
    if next_step:
        text, kb = mock_prompt(next_step)
        await edit_panel(context, text, kb)
        return next_state
    return await _mock_finish(update, context)


async def mock_quant(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _mock_score_step(update, context, "quant", M_QUANT, M_VERBAL, "verbal")


async def mock_verbal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _mock_score_step(update, context, "verbal", M_VERBAL, M_TOTAL, "total")


async def mock_total(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _mock_score_step(update, context, "total", M_TOTAL, -1, "")


async def mock_total_auto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    data = context.user_data.get("q_mock", {})
    if "quant" in data and "verbal" in data:
        data["total"] = round((data["quant"] + data["verbal"]) / 2, 1)
    return await _mock_finish(update, context)


async def _mock_finish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = context.user_data.get("q_mock", {})
    chat_id = update.effective_chat.id
    if not all(k in data for k in ("title", "quant", "verbal", "total")):
        clear_temp(context)
        await drop_old_panel(context)
        await send_panel(context, chat_id, menu_text(load_db()), menu_kb())
        return ConversationHandler.END

    db = load_db()
    db["mock_tests"].append({
        "id": f"m{int(time.time() * 1000)}",
        "date": today_str(),
        "title": data["title"],
        "quant": data["quant"],
        "verbal": data["verbal"],
        "total": data["total"],
    })
    save_json(DB_FILE, db)
    clear_temp(context)
    await drop_old_panel(context)
    text, kb = mocks_view(db)
    await send_panel(context, chat_id, text, kb)
    return ConversationHandler.END


async def mock_cancel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer("تم الإلغاء")
    clear_temp(context)
    text, kb = mocks_view(load_db())
    await safe_edit_text(q, text, kb)
    track_panel(context, q.message)
    return ConversationHandler.END


# ----------------------------- عدّاد التدريب اليومي -----------------------------
def train_view(db: dict):
    day = db["daily"].get(today_str(), {})
    stats = db["study_stats"]
    text = (
        f"**⚡ عدّاد التدريب اليومي**\n{LINE}\n\n"
        f"📅 **اليوم:** 🔢 كمي {day.get('quant', 0)} • 📖 لفظي {day.get('verbal', 0)}\n"
        f"📈 **الإجمالي:** 🔢 {stats['quant_questions']} • 📖 {stats['verbal_questions']}\n\n"
        f"سجّل ما حللته بضغطة:"
    )
    rows = [
        [
            InlineKeyboardButton("+10 كمي", callback_data="qudrat_cnt:quant:10"),
            InlineKeyboardButton("+25 كمي", callback_data="qudrat_cnt:quant:25"),
        ],
        [
            InlineKeyboardButton("+10 لفظي", callback_data="qudrat_cnt:verbal:10"),
            InlineKeyboardButton("+25 لفظي", callback_data="qudrat_cnt:verbal:25"),
        ],
    ]
    if stats.get("last"):
        rows.append([InlineKeyboardButton("↩️ تراجع عن آخر إضافة", callback_data="qudrat_undo")])
    rows.append(nav_row())
    return text, InlineKeyboardMarkup(rows)


async def train_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    text, kb = train_view(load_db())
    await safe_edit_text(q, text, kb)
    track_panel(context, q.message)


async def count_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, kind, n = q.data.split(":")
    n = int(n)
    db = load_db()
    day = db["daily"].setdefault(today_str(), {"quant": 0, "verbal": 0})
    day[kind] = day.get(kind, 0) + n
    db["study_stats"][f"{kind}_questions"] += n
    db["study_stats"]["last"] = {"kind": kind, "n": n, "date": today_str()}
    save_json(DB_FILE, db)
    await q.answer(f"✅ +{n} {'كمي' if kind == 'quant' else 'لفظي'}")
    text, kb = train_view(db)
    await safe_edit_text(q, text, kb)
    track_panel(context, q.message)


async def undo_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    db = load_db()
    last = db["study_stats"].pop("last", None)
    if last:
        kind, n = last["kind"], last["n"]
        key = f"{kind}_questions"
        db["study_stats"][key] = max(0, db["study_stats"][key] - n)
        day = db["daily"].get(last.get("date", ""), {})
        day[kind] = max(0, day.get(kind, 0) - n)
        save_json(DB_FILE, db)
        await q.answer(f"↩️ تم التراجع (-{n})")
    else:
        await q.answer("لا توجد إضافة للتراجع عنها")
    text, kb = train_view(db)
    await safe_edit_text(q, text, kb)
    track_panel(context, q.message)


# ----------------------------- بطاقة ليلة الاختبار -----------------------------
async def prep_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    text = (
        f"**🎒 بطاقة الاستعداد**\n{LINE}\n\n"
        f"🗓️ {fmt_full(EXAM_DATE)} — {EXAM_TIME}\n"
        f"📍 {EXAM_VENUE}\n\n"
        "🪪 الهوية الوطنية **الأصلية** — جهّزها من الليلة.\n"
        f"⏰ الحضور قبل {EXAM_TIME} بوقت كافٍ؛ احسب مدة الطريق إلى المقر مسبقًا.\n"
        "😴 نوم مبكر وكافٍ ليلة الاختبار، وبلا مراجعة متأخرة.\n"
        "🍳 فطور خفيف وماء قبل الخروج.\n"
        "🕒 وزّع الوقت: السؤال الصعب تعلّم عليه وانتقل، ثم ارجع له إن بقي وقت.\n"
        "📋 راجع تعليمات مركز الاختبار بشأن ما يُسمح بإدخاله.\n\n"
        "توكل على الله واستعن به 💪"
    )
    await safe_edit_text(q, text, InlineKeyboardMarkup([nav_row()]))
    track_panel(context, q.message)


# ----------------------------- التسجيل -----------------------------
def register(app) -> None:
    media = filters.Document.ALL | filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE
    content = media | (filters.TEXT & ~filters.COMMAND)
    text_only = filters.TEXT & ~filters.COMMAND
    fallbacks = [CommandHandler("start", start_fallback), CommandHandler("cancel", cancel_cmd)]

    add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_start, pattern=r"^qudrat_add$")],
        states={
            ADD_CAT: [
                CallbackQueryHandler(add_cat, pattern=r"^qudrat_addcat:(quant|verbal|general)$"),
                CallbackQueryHandler(add_cancel_cb, pattern=r"^qudrat_addcancel$"),
            ],
            ADD_COLLECT: [
                CallbackQueryHandler(add_done, pattern=r"^qudrat_adddone$"),
                CallbackQueryHandler(add_cancel_cb, pattern=r"^qudrat_addcancel$"),
                MessageHandler(content, add_collect),
            ],
            ADD_TITLE: [
                CallbackQueryHandler(add_cancel_cb, pattern=r"^qudrat_addcancel$"),
                MessageHandler(text_only, add_title),
            ],
        },
        fallbacks=fallbacks,
        allow_reentry=True,
    )

    mock_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(mock_start, pattern=r"^qudrat_mockadd$")],
        states={
            M_NAME: [MessageHandler(text_only, mock_name)],
            M_QUANT: [MessageHandler(text_only, mock_quant)],
            M_VERBAL: [MessageHandler(text_only, mock_verbal)],
            M_TOTAL: [
                CallbackQueryHandler(mock_total_auto, pattern=r"^qudrat_mocktotalauto$"),
                MessageHandler(text_only, mock_total),
            ],
        },
        fallbacks=fallbacks + [CallbackQueryHandler(mock_cancel_cb, pattern=r"^qudrat_mockcancel$")],
        allow_reentry=True,
    )

    app.add_handler(add_conv)
    app.add_handler(mock_conv)

    app.add_handler(CallbackQueryHandler(menu_cb, pattern=f"^{MENU}$"))
    app.add_handler(CallbackQueryHandler(browse_cb, pattern=r"^qudrat_browse:(all|quant|verbal|general):\d+$"))
    app.add_handler(CallbackQueryHandler(open_cb, pattern=r"^qudrat_open:\w+$"))
    app.add_handler(CallbackQueryHandler(manage_cb, pattern=r"^qudrat_manage:\d+$"))
    app.add_handler(CallbackQueryHandler(delete_ask_cb, pattern=r"^qudrat_del:\w+$"))
    app.add_handler(CallbackQueryHandler(delete_yes_cb, pattern=r"^qudrat_delyes:\w+$"))
    app.add_handler(CallbackQueryHandler(mocks_cb, pattern=r"^qudrat_mocks$"))
    app.add_handler(CallbackQueryHandler(mock_del_ask_cb, pattern=r"^qudrat_mockdel$"))
    app.add_handler(CallbackQueryHandler(mock_del_yes_cb, pattern=r"^qudrat_mockdelyes$"))
    app.add_handler(CallbackQueryHandler(train_cb, pattern=r"^qudrat_train$"))
    app.add_handler(CallbackQueryHandler(count_cb, pattern=r"^qudrat_cnt:(quant|verbal):(10|25)$"))
    app.add_handler(CallbackQueryHandler(undo_cb, pattern=r"^qudrat_undo$"))
    app.add_handler(CallbackQueryHandler(prep_cb, pattern=r"^qudrat_prep$"))
    # أزرار عملية انتهت (مثلاً بعد إعادة تشغيل البوت) تعيد لقائمة القسم بدل أن تبقى صامتة
    app.add_handler(CallbackQueryHandler(
        stale_cb,
        pattern=r"^qudrat_(adddone|addcancel|addcat:\w+|mockcancel|mocktotalauto)$",
    ))

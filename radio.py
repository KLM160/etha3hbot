"""
📻 الإذاعة المدرسية — أرشيف إذاعة ابن حميد
--------------------------------------------------
- يحتفظ بنفس بيانات الأرشيف (data.json) ونفس منطقه.
- الأرشيف الإذاعي يعرض الملفات مباشرة (بدون أقسام فرعية) مع صفحات.
- الإضافة والحذف والنقل تتم من نفس صفحة التصفح (البوت مغلق على المالك: owner_guard في main.py).
نقطة الربط الوحيدة مع main.py: TITLE و MENU ودالة register(app).
"""
import asyncio
import base64
import json
import logging
import os
import threading
import time
import uuid

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import GH_REPO, GH_TOKEN
from utils import build_paginated_keyboard, grid, home_button, safe_delete_msg, safe_edit_text

logger = logging.getLogger("bot.radio")

# ----------------------------------------------------------------------------
# الثوابت
# ----------------------------------------------------------------------------
TITLE = "📻 الإذاعة المدرسية"
MENU = "radio_menu"

DATA_FILE = "data.json"
MAX_BATCH_FILES = 10
MANAGE_PER_PAGE = 5

ADMIN_WAITING_FILES, ADMIN_WAITING_TITLE = range(2)

ABOUT_TEXT = (
    "🎙️ **عن أرشيف إذاعة ابن حميد | HR Bot**\n"
    "━━━━━━━ • 📻 • ━━━━━━━\n\n"
    "منصة رقمية لتوثيق وأرشفة المحتوى الإذاعي وتسهيل الوصول للأنشطة المدرسية.\n\n"
    "👨‍🏫 **الإشراف العام:** رائد النشاط أ.فهد\n"
    "💻 **التطوير والبرمجة:** AboZaid"
)

# مفاتيح data.json القديمة محفوظة كما هي (للحفاظ على البيانات الحالية)
DEFAULT_DATA = {"broadcasts": {}, "videos": {}, "dialogues": {}, "quizzes": {}, "general_archive": {}}

# "الأرشيف الإذاعي" عرض موحّد يجمع الأقسام القديمة في قائمة واحدة
ARCHIVE_SOURCES = ("broadcasts", "videos", "dialogues", "quizzes")
ADD_TARGET = {"archive": "broadcasts", "general_archive": "general_archive"}  # أين تُحفظ الإضافات الجديدة
VIEW_TITLES = {"archive": "📁 الأرشيف الإذاعي", "general_archive": "🗄️ الأرشيف العام"}

DATA_LOCK = threading.Lock()


# ----------------------------------------------------------------------------
# بيانات الأرشيف
# ----------------------------------------------------------------------------
def load_data() -> dict:
    with DATA_LOCK:
        if not os.path.exists(DATA_FILE):
            _save_data_locked(DEFAULT_DATA)
            return json.loads(json.dumps(DEFAULT_DATA))
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {**DEFAULT_DATA, **data}
        except Exception as e:
            logger.error("JSON Error عند قراءة %s: %s", DATA_FILE, e)
            return json.loads(json.dumps(DEFAULT_DATA))


def _save_data_locked(data: dict) -> None:
    tmp_path = f"{DATA_FILE}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    os.replace(tmp_path, DATA_FILE)


def save_data(data: dict) -> None:
    with DATA_LOCK:
        _save_data_locked(data)


def new_item_id() -> str:
    return f"item_{int(time.time())}_{uuid.uuid4().hex[:8]}"


def sync_to_github() -> None:
    if not (GH_TOKEN and GH_REPO):
        return
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{DATA_FILE}"
    headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        with DATA_LOCK:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                content = base64.b64encode(f.read().encode()).decode()

        get_res = requests.get(url, headers=headers, timeout=15)
        sha = get_res.json().get("sha") if get_res.status_code == 200 else None

        payload = {"message": "🔄 التحديث التلقائي للأرشيف", "content": content}
        if sha:
            payload["sha"] = sha

        put_res = requests.put(url, headers=headers, json=payload, timeout=15)
        if put_res.status_code not in (200, 201):
            logger.error("فشلت مزامنة GitHub (status=%s): %s", put_res.status_code, put_res.text[:500])
        else:
            logger.info("✅ تمت مزامنة الأرشيف مع GitHub بنجاح.")
    except requests.RequestException as e:
        logger.error("GitHub Network Error: %s", e)
    except Exception as e:
        logger.error("GitHub Error غير متوقع: %s", e)


def view_of(cat_key: str) -> str:
    """يحوّل أي مفتاح قسم (قديم أو جديد) إلى العرض المناسب: archive أو general_archive."""
    return "general_archive" if cat_key == "general_archive" else "archive"


def collect_items(db: dict, view: str) -> list:
    """قائمة مسطّحة [(cat_key, item_id, info)] للعرض المطلوب."""
    keys = ("general_archive",) if view == "general_archive" else ARCHIVE_SOURCES
    return [(k, iid, info) for k in keys for iid, info in db.get(k, {}).items()]


def find_item_cat(db: dict, view: str, iid: str):
    for cat, i, _ in collect_items(db, view):
        if i == iid:
            return cat
    return None


def md_safe(text: str) -> str:
    for ch in "*_`[":
        text = text.replace(ch, "")
    return text


def _page(value) -> int:
    return int(value) if str(value).isdigit() else 0


def _sync_async() -> None:
    asyncio.create_task(asyncio.to_thread(sync_to_github))


# ----------------------------------------------------------------------------
# واجهة التصفح
# ----------------------------------------------------------------------------
BACK_RADIO = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 قائمة الإذاعة", callback_data="radio_menu")]])
BACK_ROW = [InlineKeyboardButton("🔙 قائمة الإذاعة", callback_data="radio_menu")]


async def radio_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup(
        grid([
            InlineKeyboardButton(VIEW_TITLES["archive"], callback_data="cat_archive:0"),
            InlineKeyboardButton(VIEW_TITLES["general_archive"], callback_data="cat_general_archive:0"),
            InlineKeyboardButton("ℹ️ عن الأرشيف", callback_data="about_info"),
        ]) + [[home_button()]]
    )
    text = (
        "📻 **الإذاعة المدرسية**\n"
        "━━━━━━━ • 📻 • ━━━━━━━\n\n"
        "أرشيف إذاعة ابن حميد لتوثيق المحتوى الإذاعي.\nاختر من القائمة:"
    )
    await safe_edit_text(update.callback_query, text, kb)


async def render_view(query, view: str, page: int = 0) -> None:
    """يعرض ملفات الأرشيف مباشرة مع الصفحات وأزرار الإضافة/الإدارة أسفلها."""
    items = collect_items(load_data(), view)
    add_btn = InlineKeyboardButton("➕ إضافة محتوى جديد", callback_data=f"adm_add:{view}")
    text = f"📂 **{VIEW_TITLES[view]}**\n━━━━━━━ • 📑 • ━━━━━━━\n\n"

    if not items:
        text += "⚠️ **لا توجد ملفات مضافة حالياً.**"
        kb = InlineKeyboardMarkup([[add_btn], BACK_ROW])
    else:
        text += "👇 **قائمة المحتوى المتاح:**"
        buttons = [
            (iid, InlineKeyboardButton(f"📥 {idx}. {info['title']}", callback_data=f"dl:{cat}:{iid}"))
            for idx, (cat, iid, info) in enumerate(items, start=1)
        ]
        manage_btn = InlineKeyboardButton("🗑️ إدارة وحذف", callback_data=f"adm_manage:{view}:{page}")
        kb = build_paginated_keyboard(buttons, page, f"cat_{view}", [[add_btn, manage_btn], BACK_ROW])
    await safe_edit_text(query, text, kb)


async def render_manage(query, view: str, page: int = 0, notice: str = "") -> None:
    """وضع الإدارة: لكل عنصر زر حذف وزر نقل للأرشيف العام."""
    items = collect_items(load_data(), view)
    back_btn = InlineKeyboardButton("✅ تم، رجوع للقائمة", callback_data=f"cat_{view}:{page}")

    if not items:
        text = (notice + "\n\n" if notice else "") + "⚠️ **لا توجد ملفات للإدارة.**"
        await safe_edit_text(query, text, InlineKeyboardMarkup([[InlineKeyboardButton("✅ رجوع للقائمة", callback_data=f"cat_{view}:0")]]))
        return

    pages = (len(items) + MANAGE_PER_PAGE - 1) // MANAGE_PER_PAGE
    page = max(0, min(page, pages - 1))
    start = page * MANAGE_PER_PAGE
    can_move = view != "general_archive"

    rows = []
    for idx, (cat, iid, info) in enumerate(items[start:start + MANAGE_PER_PAGE], start=start + 1):
        row = [InlineKeyboardButton(f"🗑️ {idx}. {info['title'][:22]}", callback_data=f"admx:del:{view}:{page}:{iid}")]
        if can_move:
            row.append(InlineKeyboardButton("📦 نقل", callback_data=f"admx:mv:{view}:{page}:{iid}"))
        rows.append(row)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ السابق", callback_data=f"adm_manage:{view}:{page - 1}"))
    if pages > 1:
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("التالي ▶️", callback_data=f"adm_manage:{view}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("✅ تم، رجوع للقائمة", callback_data=f"cat_{view}:{page}")])

    hint = "🗑️ للحذف" + (" • 📦 للنقل إلى الأرشيف العام" if can_move else "")
    text = (notice + "\n\n" if notice else "") + f"🛠️ **إدارة {VIEW_TITLES[view]}**\n━━━━━━━ • ⚙️ • ━━━━━━━\n\n{hint}"
    await safe_edit_text(query, text, InlineKeyboardMarkup(rows))


async def radio_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query, data = update.callback_query, update.callback_query.data
    await query.answer()

    if data == "radio_menu":
        await radio_menu(update, context)

    elif data == "about_info":
        await safe_edit_text(query, ABOUT_TEXT, BACK_RADIO)

    elif data == "archive_menu":  # توافق مع الأزرار القديمة
        await render_view(query, "archive", 0)

    elif data.startswith("cat_"):
        # الصيغة: cat_<key>:<page> — أي مفتاح قديم (broadcasts/videos/...) يُحوَّل إلى الأرشيف الإذاعي
        cat_key, _, page_str = data[len("cat_"):].partition(":")
        await render_view(query, view_of(cat_key), _page(page_str))

    elif data.startswith("dl:"):
        _, cat_key, item_id = data.split(":", 2)
        item = load_data().get(cat_key, {}).get(item_id)
        if not item:
            await safe_edit_text(query, "⚠️ لم يعد هذا المحتوى متاحًا.", BACK_RADIO)
            return

        await safe_edit_text(query, "⏳ **جاري جلب المحتوى...**")
        chat_id = query.message.chat_id
        await context.bot.send_message(chat_id=chat_id, text=f"📦 **المحتوى المطلوب:** {item['title']}")

        failures = 0
        for f in item.get("files", []):
            t, fid, txt = f.get("type"), f.get("file_id"), f.get("text")
            try:
                if t == "text":
                    await context.bot.send_message(chat_id, txt, parse_mode="Markdown")
                elif t == "video":
                    await context.bot.send_video(chat_id, fid)
                elif t == "photo":
                    await context.bot.send_photo(chat_id, fid)
                elif t == "audio":
                    await context.bot.send_audio(chat_id, fid)
                else:
                    await context.bot.send_document(chat_id, fid)
            except TelegramError as e:
                failures += 1
                logger.warning("فشل إرسال ملف ضمن '%s': %s", item.get("title"), e)
            await asyncio.sleep(0.3)

        await safe_delete_msg(context, chat_id, query.message.message_id)

        footer = f"✨ **تم إرسال المحتوى:**\n📌 **{item['title']}**"
        if failures:
            footer += f"\n⚠️ تعذّر إرسال {failures} من العناصر (قد تكون محذوفة من تيليجرام)."
        nav = [[
            InlineKeyboardButton("🔙 رجوع للقائمة", callback_data=f"cat_{view_of(cat_key)}:0"),
            InlineKeyboardButton("📻 قائمة الإذاعة", callback_data="radio_menu"),
        ]]
        await context.bot.send_message(chat_id=chat_id, text=footer, reply_markup=InlineKeyboardMarkup(nav), parse_mode="Markdown")


# ----------------------------------------------------------------------------
# الإضافة (محادثة) — تبدأ من زر «➕ إضافة محتوى جديد» في صفحة التصفح
# ----------------------------------------------------------------------------
async def admin_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    view = query.data.split(":", 1)[1]
    context.user_data["admin_origin"] = view
    context.user_data["admin_target_cat"] = ADD_TARGET.get(view, "broadcasts")
    context.user_data["admin_files_batch"] = []
    context.user_data.pop("prompt_msg_id", None)

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ اكتفيت، انتقل للعنوان", callback_data="adm_done_files")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="adm_cancel")],
    ])
    await safe_edit_text(
        query,
        f"📥 **الإضافة إلى {VIEW_TITLES.get(view, '')}**\n\nأرسل الملفات أو النصوص الآن (حد أقصى {MAX_BATCH_FILES}):",
        kb,
    )
    return ADMIN_WAITING_FILES


async def admin_receive_files_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    batch = context.user_data.get("admin_files_batch", [])
    if len(batch) >= MAX_BATCH_FILES:
        await update.message.reply_text("⚠️ وصلت للحد الأقصى!")
        return ADMIN_WAITING_FILES

    msg = update.message
    fid, ftype, txt = None, "document", None
    if msg.document:
        fid, ftype = msg.document.file_id, "document"
    elif msg.video:
        fid, ftype = msg.video.file_id, "video"
    elif msg.photo:
        fid, ftype = msg.photo[-1].file_id, "photo"
    elif msg.audio or msg.voice:
        fid, ftype = (msg.audio or msg.voice).file_id, "audio"
    elif msg.text:
        txt, ftype = msg.text, "text"
    else:
        await msg.reply_text("⚠️ نوع الملف غير مدعوم، أرسل نصًا أو صورة أو فيديو أو صوت أو مستند.")
        return ADMIN_WAITING_FILES

    if "prompt_msg_id" in context.user_data:
        await safe_delete_msg(context, chat_id, context.user_data["prompt_msg_id"])

    batch.append({"file_id": fid, "type": ftype, "text": txt})
    context.user_data["admin_files_batch"] = batch

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ اكتفيت بالمحتوى، انتقل لتحديد العنوان", callback_data="adm_done_files")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="adm_cancel")],
    ])
    new_msg = await msg.reply_text(
        f"📥 **تم استلام المادة ({len(batch)}/{MAX_BATCH_FILES})!** أرسل التالية أو اضغط الزر:",
        reply_markup=kb,
        parse_mode="Markdown",
    )
    context.user_data["prompt_msg_id"] = new_msg.message_id
    return ADMIN_WAITING_FILES


async def admin_finish_files_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="adm_cancel")]])

    if not context.user_data.get("admin_files_batch"):
        await safe_edit_text(query, "❌ لم ترسل أي مواد!", cancel_kb)
        return ADMIN_WAITING_FILES

    await safe_edit_text(query, "✍️ أرسل الآن **الاسم/العنوان الظاهر للمجموعة**:", cancel_kb)
    return ADMIN_WAITING_TITLE


async def admin_save_file_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    title = update.message.text.strip()
    await safe_delete_msg(context, chat_id, update.message.message_id)

    if not title:
        await context.bot.send_message(chat_id, "⚠️ العنوان لا يمكن أن يكون فارغًا، أعد المحاولة:")
        return ADMIN_WAITING_TITLE

    target_cat = context.user_data.get("admin_target_cat")
    batch = context.user_data.get("admin_files_batch")
    origin = context.user_data.get("admin_origin", "archive")
    if not target_cat or not batch:
        await context.bot.send_message(chat_id, "❌ حدث خطأ: انتهت الجلسة. ابدأ من جديد عبر /start.")
        context.user_data.clear()
        return ConversationHandler.END

    db = load_data()
    db.setdefault(target_cat, {})[new_item_id()] = {"title": title, "files": batch}
    save_data(db)
    _sync_async()
    context.user_data.clear()

    await context.bot.send_message(
        chat_id,
        f"🎉 **تمت الإضافة بنجاح!**\n📌 **العنوان:** `{title}`",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📂 عودة للقائمة", callback_data=f"cat_{origin}:0")]]),
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء الإضافة والرجوع لنفس صفحة التصفح التي بدأ منها المالك."""
    query = update.callback_query
    await query.answer()
    origin = context.user_data.get("admin_origin", "archive")
    context.user_data.clear()
    await render_view(query, origin, 0)
    return ConversationHandler.END


async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 **تم الإلغاء.**", parse_mode="Markdown")
    context.user_data.clear()
    return ConversationHandler.END


# ----------------------------------------------------------------------------
# الإدارة المباشرة (حذف / نقل للأرشيف العام) من صفحة التصفح
# ----------------------------------------------------------------------------
async def manage_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """adm_manage:<view>:<page>  |  admx:<op>:<view>:<page>:<item_id>  (op = del / ydel / mv)"""
    query, data = update.callback_query, update.callback_query.data
    await query.answer()

    if data.startswith("adm_manage:"):
        _, view, page = data.split(":", 2)
        await render_manage(query, view_of(view), _page(page))
        return

    _, op, view, page, iid = data.split(":", 4)
    view, page = view_of(view), _page(page)
    db = load_data()
    cat = find_item_cat(db, view, iid)
    if cat is None:
        await render_manage(query, view, page, notice="⚠️ هذا العنصر لم يعد موجودًا.")
        return

    title = db[cat][iid]["title"]
    safe_title = md_safe(title)

    if op == "del":  # شاشة تأكيد قبل الحذف النهائي
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ نعم، احذف", callback_data=f"admx:ydel:{view}:{page}:{iid}"),
            InlineKeyboardButton("❌ تراجع", callback_data=f"adm_manage:{view}:{page}"),
        ]])
        await safe_edit_text(query, f"⚠️ **تأكيد الحذف**\n\nهل تريد حذف «{safe_title}» نهائيًا؟", kb)

    elif op == "ydel":
        del db[cat][iid]
        save_data(db)
        _sync_async()
        await render_manage(query, view, page, notice=f"🗑️ **تم حذف:** {safe_title}")

    elif op == "mv":
        if cat == "general_archive":
            await render_manage(query, view, page, notice="ℹ️ العنصر موجود في الأرشيف العام أصلًا.")
            return
        db.setdefault("general_archive", {})[iid] = db[cat].pop(iid)
        save_data(db)
        _sync_async()
        await render_manage(query, view, page, notice=f"📦 **تم النقل للأرشيف العام:** {safe_title}")


# ----------------------------------------------------------------------------
# الربط مع main.py
# ----------------------------------------------------------------------------
def register(app) -> None:
    add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_start, pattern=r"^adm_add:(archive|general_archive)$")],
        states={
            ADMIN_WAITING_FILES: [
                MessageHandler(
                    (filters.Document.ALL | filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE | filters.TEXT)
                    & ~filters.COMMAND,
                    admin_receive_files_step,
                ),
                CallbackQueryHandler(admin_finish_files_step, pattern="^adm_done_files$"),
            ],
            ADMIN_WAITING_TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_save_file_step),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_conv),
            CallbackQueryHandler(admin_cancel, pattern="^adm_cancel$"),
        ],
        per_user=True,
    )
    app.add_handler(add_conv)
    app.add_handler(CallbackQueryHandler(manage_handler, pattern=r"^(adm_manage:|admx:)"))
    app.add_handler(CallbackQueryHandler(radio_handler, pattern=r"^(radio_menu|archive_menu|about_info|cat_|dl:)"))

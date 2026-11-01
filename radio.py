"""
📻 الإذاعة المدرسية — أرشيف إذاعة ابن حميد
--------------------------------------------------
قسمان فقط (data/radio.json):  "archive" = الأرشيف  |  "general_archive" = الأرشيف العام
- الملفات تُعرض مباشرة مع صفحات، والإضافة/الحذف/النقل من نفس صفحة التصفح.
- كل تعديل يُحفظ محليًا ثم يُرفع إلى GitHub برسالة commit توضّح نوع التعديل.
نقطة الربط مع main.py: TITLE و MENU و register(app) و restore_from_github().
"""
import asyncio
import json
import logging
import os
import threading
import time
import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import ALLOWED_USER_ID, GH_DATA_DIR
from utils import (
    build_paginated_keyboard,
    drop_old_panel,
    github_enabled,
    github_pull,
    github_push,
    grid,
    home_button,
    md_safe,
    safe_delete_msg,
    safe_edit_text,
    track_panel,
)

logger = logging.getLogger("bot.radio")

# ----------------------------------------------------------------------------
# الثوابت
# ----------------------------------------------------------------------------
TITLE = "📻 الإذاعة المدرسية"
MENU = "radio_menu"

DATA_FILE = "radio.json"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))     # مسار ثابت مهما كان مجلد التشغيل
DATA_PATH = os.path.join(BASE_DIR, "data", DATA_FILE)     # محليًا: data/radio.json
REPO_PATH = f"{GH_DATA_DIR}/{DATA_FILE}"                  # في GitHub: data/radio.json
LEGACY_FILE = "data.json"                                 # الاسم القديم (للترحيل لمرة واحدة فقط)
LEGACY_PATH = os.path.join(BASE_DIR, LEGACY_FILE)
MAX_BATCH_FILES = 10
MANAGE_PER_PAGE = 5

WAITING_FILES, WAITING_TITLE = range(2)

VIEWS = {"archive": "📁 الأرشيف", "general_archive": "🗄️ الأرشيف العام"}  # عناوين الأزرار
VIEW_NAMES = {"archive": "الأرشيف", "general_archive": "الأرشيف العام"}    # للنصوص ورسائل GitHub
LEGACY_KEYS = ("broadcasts", "videos", "dialogues", "quizzes")            # أقسام قديمة تُدمج في «الأرشيف»

ABOUT_TEXT = (
    "🎙️ **عن أرشيف إذاعة ابن حميد | HR Bot**\n"
    "━━━━━━━ • 📻 • ━━━━━━━\n\n"
    "منصة رقمية لتوثيق وأرشفة المحتوى الإذاعي وتسهيل الوصول للأنشطة المدرسية.\n\n"
    "👨‍🏫 **الإشراف العام:** رائد النشاط أ.فهد\n"
    "💻 **التطوير والبرمجة:** AboZaid"
)

DATA_LOCK = threading.Lock()
_BG_TASKS: set = set()  # مراجع لمهام المزامنة حتى لا يجمعها GC قبل انتهائها

BACK_ROW = [InlineKeyboardButton("🔙 قائمة الإذاعة", callback_data=MENU)]
BACK_RADIO = InlineKeyboardMarkup([BACK_ROW])


# ----------------------------------------------------------------------------
# البيانات
# ----------------------------------------------------------------------------
def _normalize(raw) -> tuple:
    """يرجع (بيانات بالشكل الجديد، هل كانت قديمة؟) — يدمج الأقسام القديمة في archive."""
    raw = raw if isinstance(raw, dict) else {}
    data = {v: dict(raw.get(v) or {}) for v in VIEWS}
    legacy = [k for k in LEGACY_KEYS if k in raw]
    for k in legacy:
        data["archive"].update(raw[k] or {})
    return data, bool(legacy)


def _save_locked(data: dict) -> None:
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)  # مجلد data/ قد لا يوجد بعد
    tmp = f"{DATA_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    os.replace(tmp, DATA_PATH)


def load_data() -> dict:
    with DATA_LOCK:
        if not os.path.exists(DATA_PATH):
            data, _ = _normalize({})
            _save_locked(data)
            return data
        try:
            with open(DATA_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:  # noqa: BLE001
            logger.error("JSON تالف في %s: %s — نُسخ إلى .bak", DATA_PATH, e)
            try:
                os.replace(DATA_PATH, f"{DATA_PATH}.bak")  # حتى لا تُكتب فوقه بيانات فارغة
            except OSError:
                pass
            return _normalize({})[0]
        data, was_legacy = _normalize(raw)
        if was_legacy:
            _save_locked(data)
        return data


def save_data(data: dict) -> None:
    with DATA_LOCK:
        _save_locked(data)


def new_item_id() -> str:
    return f"item_{int(time.time())}_{uuid.uuid4().hex[:8]}"


def item_timestamp(iid: str) -> int:
    try:
        return int(str(iid).split("_")[1])
    except (IndexError, ValueError):
        return 0


def sorted_items(db: dict, view: str) -> list:
    """[(item_id, info)] مرتّبة من الأقدم إلى الأحدث."""
    items = [(iid, info) for iid, info in (db.get(view) or {}).items() if isinstance(info, dict)]
    return sorted(items, key=lambda x: item_timestamp(x[0]))


def item_title(info) -> str:
    if isinstance(info, dict) and info.get("title"):
        return str(info["title"])
    return "بدون عنوان"


def one_line(text: str) -> str:
    return " ".join(str(text).split())


def short(text: str, n: int = 50) -> str:
    t = one_line(text)
    return t if len(t) <= n else t[: n - 1] + "…"


def parse_view(value: str) -> str:
    return value if value in VIEWS else "archive"


def parse_page(value) -> int:
    return int(value) if str(value).isdigit() else 0


# ----------------------------------------------------------------------------
# GitHub
# ----------------------------------------------------------------------------
def _local_is_empty() -> bool:
    """True إن لم يوجد data/radio.json محليًا أو كان بلا أي عنصر."""
    if not os.path.exists(DATA_PATH):
        return True
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            data, _ = _normalize(json.load(f))
    except Exception:  # noqa: BLE001
        return True
    return not any(data.values())


def _migrate_local_legacy() -> None:
    """ترحيل لمرة واحدة: data.json القديم (محليًا) ← data/radio.json إن كان الجديد غير موجود."""
    if os.path.exists(DATA_PATH) or not os.path.exists(LEGACY_PATH):
        return
    try:
        with open(LEGACY_PATH, "r", encoding="utf-8") as f:
            data, _ = _normalize(json.load(f))
        with DATA_LOCK:
            _save_locked(data)
        logger.info("✅ تم ترحيل %s إلى %s", LEGACY_FILE, DATA_PATH)
    except Exception as e:  # noqa: BLE001
        logger.error("تعذّر ترحيل %s: %s", LEGACY_PATH, e)


def restore_from_github() -> None:
    """عند التشغيل: يجلب آخر data/radio.json من GitHub (قرص Render مؤقت ونسخة النشر قد تكون أقدم)."""
    _migrate_local_legacy()
    if not github_enabled():
        logger.warning("⚠️ GH_TOKEN/GH_REPO غير مضبوطين: لن تُحفظ تعديلات الأرشيف بعد إعادة التشغيل!")
        return

    raw = github_pull(REPO_PATH)
    migrated = False
    if raw is None and _local_is_empty():  # أول تشغيل بعد التسمية: نجلب data.json القديم من الجذر
        raw = github_pull(LEGACY_FILE)
        migrated = raw is not None
        if migrated:
            logger.info("↪️ لا يوجد %s في GitHub بعد، سيُرحَّل المحتوى من %s.", REPO_PATH, LEGACY_FILE)
    if raw is None:
        logger.warning("لم يتم جلب %s من GitHub، سيُستخدم الملف المحلي.", DATA_FILE)
        return
    try:
        remote = json.loads(raw.decode("utf-8"))
        if not isinstance(remote, dict):
            raise ValueError("بنية غير متوقعة")
    except Exception as e:  # noqa: BLE001
        logger.error("%s في GitHub غير صالح، سيُستخدم الملف المحلي: %s", DATA_FILE, e)
        return
    data, was_legacy = _normalize(remote)
    with DATA_LOCK:
        _save_locked(data)
    logger.info("✅ تم تحميل أحدث أرشيف من GitHub.")
    if migrated:  # رفع النسخة المرحَّلة إلى المسار الجديد data/radio.json
        github_push(DATA_PATH, REPO_PATH, "🚚 ترحيل data.json إلى data/radio.json [skip render]")
    elif was_legacy:  # نسخة GitHub بالشكل القديم: ننظّفها مرة واحدة
        github_push(DATA_PATH, REPO_PATH, "🧹 تنظيف: دمج الأقسام القديمة في «الأرشيف» [skip render]")


async def _sync_and_report(context, message: str) -> None:
    ok, msg = await asyncio.to_thread(github_push, DATA_PATH, REPO_PATH, message)
    if ok:
        return
    try:  # تنبيه صريح للمالك بدل الفشل الصامت
        await context.bot.send_message(
            ALLOWED_USER_ID,
            f"⚠️ تم الحفظ محليًا لكن فشلت المزامنة مع GitHub، وقد يرجع التعديل بعد إعادة التشغيل.\n\nالسبب: {msg}",
        )
    except TelegramError:
        pass


def sync(context, action: str) -> None:
    """يرفع data/radio.json في الخلفية؛ action = وصف التعديل ويصير رسالة الـ commit."""
    task = asyncio.create_task(_sync_and_report(context, f"{action} [skip render]"))
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


# ----------------------------------------------------------------------------
# واجهة التصفح
# ----------------------------------------------------------------------------
async def radio_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup(
        grid([
            InlineKeyboardButton(VIEWS["archive"], callback_data="cat_archive:0"),
            InlineKeyboardButton(VIEWS["general_archive"], callback_data="cat_general_archive:0"),
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
    items = sorted_items(load_data(), view)
    add_btn = InlineKeyboardButton("➕ إضافة محتوى جديد", callback_data=f"adm_add:{view}")
    text = f"📂 **{VIEWS[view]}**\n━━━━━━━ • 📑 • ━━━━━━━\n\n"

    if not items:
        text += "⚠️ **لا توجد ملفات مضافة حالياً.**"
        kb = InlineKeyboardMarkup([[add_btn], BACK_ROW])
    else:
        text += "👇 **قائمة المحتوى المتاح:**"
        buttons = [
            InlineKeyboardButton(f"📥 {idx}. {short(item_title(info), 55)}", callback_data=f"dl:{view}:{iid}")
            for idx, (iid, info) in enumerate(items, start=1)
        ]
        manage_btn = InlineKeyboardButton("🗑️ إدارة وحذف", callback_data=f"adm_manage:{view}:{page}")
        kb = build_paginated_keyboard(buttons, page, f"cat_{view}", [[add_btn, manage_btn], BACK_ROW])
    await safe_edit_text(query, text, kb)


async def render_manage(query, view: str, page: int = 0, notice: str = "") -> None:
    """وضع الإدارة: لكل عنصر زر حذف وزر نقل للقسم الآخر."""
    items = sorted_items(load_data(), view)
    prefix = notice + "\n\n" if notice else ""
    if not items:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ رجوع للقائمة", callback_data=f"cat_{view}:0")]])
        await safe_edit_text(query, prefix + "⚠️ **لا توجد ملفات للإدارة.**", kb)
        return

    pages = (len(items) + MANAGE_PER_PAGE - 1) // MANAGE_PER_PAGE
    page = max(0, min(page, pages - 1))
    start = page * MANAGE_PER_PAGE
    move_label = "📦 للأرشيف العام" if view == "archive" else "📤 للأرشيف"

    rows = []
    for idx, (iid, info) in enumerate(items[start:start + MANAGE_PER_PAGE], start=start + 1):
        rows.append([
            InlineKeyboardButton(f"🗑️ {idx}. {short(item_title(info), 20)}", callback_data=f"admx:del:{view}:{page}:{iid}"),
            InlineKeyboardButton(move_label, callback_data=f"admx:mv:{view}:{page}:{iid}"),
        ])

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

    text = f"{prefix}🛠️ **إدارة {VIEWS[view]}**\n━━━━━━━ • ⚙️ • ━━━━━━━\n\n🗑️ للحذف • {move_label} للنقل"
    await safe_edit_text(query, text, InlineKeyboardMarkup(rows))


async def _send_text(bot, chat_id: int, text: str) -> None:
    try:
        await bot.send_message(chat_id, text, parse_mode="Markdown")
    except BadRequest:  # تنسيق Markdown غير صالح في نص المستخدم
        await bot.send_message(chat_id, text)


async def radio_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query, data = update.callback_query, update.callback_query.data
    await query.answer()

    if data == MENU:
        await radio_menu(update, context)

    elif data == "about_info":
        await safe_edit_text(query, ABOUT_TEXT, BACK_RADIO)

    elif data.startswith("cat_"):  # cat_<view>:<page>
        view, _, page = data[4:].partition(":")
        await render_view(query, parse_view(view), parse_page(page))

    elif data.startswith("dl:"):  # dl:<view>:<item_id>
        _, view, iid = data.split(":", 2)
        view = parse_view(view)
        item = (load_data().get(view) or {}).get(iid)
        if not item:
            await safe_edit_text(query, "⚠️ لم يعد هذا المحتوى متاحًا.", BACK_RADIO)
            return

        await safe_edit_text(query, "⏳ **جاري جلب المحتوى...**")
        chat_id = query.message.chat_id
        title = item_title(item)
        await context.bot.send_message(chat_id, f"📦 **المحتوى المطلوب:** {md_safe(title)}", parse_mode="Markdown")

        failures = 0
        for f in item.get("files") or []:
            kind, fid, txt = f.get("type"), f.get("file_id"), f.get("text")
            try:
                if kind == "text":
                    if txt:
                        await _send_text(context.bot, chat_id, txt)
                elif kind == "video":
                    await context.bot.send_video(chat_id, fid)
                elif kind == "photo":
                    await context.bot.send_photo(chat_id, fid)
                elif kind == "audio":
                    await context.bot.send_audio(chat_id, fid)
                else:
                    await context.bot.send_document(chat_id, fid)
            except TelegramError as e:
                failures += 1
                logger.warning("فشل إرسال ملف ضمن '%s': %s", title, e)
            await asyncio.sleep(0.3)

        await safe_delete_msg(context, chat_id, query.message.message_id)

        footer = f"✨ **تم إرسال المحتوى:**\n📌 **{md_safe(title)}**"
        if failures:
            footer += f"\n⚠️ تعذّر إرسال {failures} من العناصر (قد تكون محذوفة من تيليجرام)."
        nav = [[
            InlineKeyboardButton("🔙 رجوع للقائمة", callback_data=f"cat_{view}:0"),
            InlineKeyboardButton("📻 قائمة الإذاعة", callback_data=MENU),
        ]]
        footer_msg = await context.bot.send_message(
            chat_id, footer, reply_markup=InlineKeyboardMarkup(nav), parse_mode="Markdown"
        )
        track_panel(context, footer_msg)


# ----------------------------------------------------------------------------
# الإضافة (محادثة) — تبدأ من زر «➕ إضافة محتوى جديد»
# ----------------------------------------------------------------------------
def _add_kb(done_text: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(done_text, callback_data="adm_done_files")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="adm_cancel")],
    ])


async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    view = parse_view(query.data.split(":", 1)[1])
    context.user_data["adm_view"] = view
    context.user_data["adm_batch"] = []
    await safe_edit_text(
        query,
        f"📥 **الإضافة إلى {VIEWS[view]}**\n\nأرسل الملفات أو النصوص الآن (حد أقصى {MAX_BATCH_FILES}):",
        _add_kb("✅ اكتفيت، انتقل للعنوان"),
    )
    return WAITING_FILES


async def add_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    batch = context.user_data.setdefault("adm_batch", [])
    if len(batch) >= MAX_BATCH_FILES:
        await update.message.reply_text("⚠️ وصلت للحد الأقصى! اضغط «انتقل للعنوان».")
        return WAITING_FILES

    msg = update.message
    fid, kind, txt = None, "document", None
    if msg.document:
        fid, kind = msg.document.file_id, "document"
    elif msg.video:
        fid, kind = msg.video.file_id, "video"
    elif msg.photo:
        fid, kind = msg.photo[-1].file_id, "photo"
    elif msg.audio or msg.voice:
        fid, kind = (msg.audio or msg.voice).file_id, "audio"
    elif msg.text:
        txt, kind = msg.text, "text"
    else:
        await msg.reply_text("⚠️ نوع الملف غير مدعوم، أرسل نصًا أو صورة أو فيديو أو صوت أو مستند.")
        return WAITING_FILES

    await drop_old_panel(context)  # الواجهة السابقة (شاشة الإضافة أو رسالة الاستلام السابقة)
    batch.append({"file_id": fid, "type": kind, "text": txt})

    new_msg = await msg.reply_text(
        f"📥 **تم استلام المادة ({len(batch)}/{MAX_BATCH_FILES})!** أرسل التالية أو اضغط الزر:",
        reply_markup=_add_kb("✅ اكتفيت بالمحتوى، انتقل لتحديد العنوان"),
        parse_mode="Markdown",
    )
    track_panel(context, new_msg)
    return WAITING_FILES


async def add_ask_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="adm_cancel")]])

    if not context.user_data.get("adm_batch"):
        await safe_edit_text(query, "❌ لم ترسل أي مواد! أرسل ملفًا أو نصًا أولًا.", cancel_kb)
        return WAITING_FILES

    await safe_edit_text(query, "✍️ أرسل الآن **الاسم/العنوان الظاهر للمجموعة**:", cancel_kb)
    return WAITING_TITLE


async def add_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    title = update.message.text.strip()
    await safe_delete_msg(context, chat_id, update.message.message_id)

    if not title:
        await context.bot.send_message(chat_id, "⚠️ العنوان لا يمكن أن يكون فارغًا، أعد المحاولة:")
        return WAITING_TITLE

    view = context.user_data.get("adm_view")
    batch = context.user_data.get("adm_batch")
    if view not in VIEWS or not batch:
        await context.bot.send_message(chat_id, "❌ انتهت الجلسة. ابدأ من جديد عبر /start.")
        context.user_data.clear()
        return ConversationHandler.END

    db = load_data()
    db[view][new_item_id()] = {"title": title, "files": batch}
    save_data(db)
    sync(context, f"➕ إضافة «{short(title)}» إلى {VIEW_NAMES[view]} ({len(batch)} ملف)")
    context.user_data.clear()

    await drop_old_panel(context)  # شاشة طلب العنوان
    done = await context.bot.send_message(
        chat_id,
        f"🎉 **تمت الإضافة بنجاح!**\n📌 **العنوان:** {md_safe(title)}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📂 عودة للقائمة", callback_data=f"cat_{view}:0")]]),
        parse_mode="Markdown",
    )
    track_panel(context, done)
    return ConversationHandler.END


async def add_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    view = parse_view(context.user_data.get("adm_view", "archive"))
    context.user_data.clear()
    await render_view(query, view, 0)
    return ConversationHandler.END


async def add_cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 **تم الإلغاء.**", parse_mode="Markdown")
    context.user_data.clear()
    return ConversationHandler.END


async def end_on_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start أثناء الإضافة: ينهي المحادثة بصمت (الرد تتولاه start في main.py عبر group=1)."""
    context.user_data.clear()
    return ConversationHandler.END


async def stale_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أزرار إضافة عالقة بعد إعادة تشغيل البوت (المحادثة لم تعد موجودة)."""
    query = update.callback_query
    await query.answer("انتهت الجلسة")
    context.user_data.clear()
    await render_view(query, "archive", 0)


# ----------------------------------------------------------------------------
# الإدارة المباشرة (حذف / نقل)
# ----------------------------------------------------------------------------
async def manage_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """adm_manage:<view>:<page>  |  admx:<op>:<view>:<page>:<item_id>   (op = del / ydel / mv)"""
    query, data = update.callback_query, update.callback_query.data
    await query.answer()

    if data.startswith("adm_manage:"):
        _, view, page = data.split(":", 2)
        await render_manage(query, parse_view(view), parse_page(page))
        return

    _, op, view, page, iid = data.split(":", 4)
    view, page = parse_view(view), parse_page(page)
    db = load_data()
    info = db[view].get(iid)
    if info is None:
        await render_manage(query, view, page, notice="⚠️ هذا العنصر لم يعد موجودًا.")
        return

    title = item_title(info)
    safe_title = md_safe(short(title, 60))

    if op == "del":  # شاشة تأكيد قبل الحذف النهائي
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ نعم، احذف", callback_data=f"admx:ydel:{view}:{page}:{iid}"),
            InlineKeyboardButton("❌ تراجع", callback_data=f"adm_manage:{view}:{page}"),
        ]])
        await safe_edit_text(query, f"⚠️ **تأكيد الحذف**\n\nهل تريد حذف «{safe_title}» نهائيًا؟", kb)

    elif op == "ydel":
        del db[view][iid]
        save_data(db)
        sync(context, f"🗑️ حذف «{short(title)}» من {VIEW_NAMES[view]}")
        await render_manage(query, view, page, notice=f"🗑️ **تم حذف:** {safe_title}")

    elif op == "mv":
        dst = "general_archive" if view == "archive" else "archive"
        db[dst][iid] = db[view].pop(iid)
        save_data(db)
        sync(context, f"📦 نقل «{short(title)}» من {VIEW_NAMES[view]} إلى {VIEW_NAMES[dst]}")
        await render_manage(query, view, page, notice=f"📦 **تم النقل إلى {VIEW_NAMES[dst]}:** {safe_title}")


# ----------------------------------------------------------------------------
# الربط مع main.py
# ----------------------------------------------------------------------------
def register(app) -> None:
    add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_start, pattern=r"^adm_add:(archive|general_archive)$")],
        states={
            WAITING_FILES: [
                MessageHandler(
                    (filters.Document.ALL | filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE | filters.TEXT)
                    & ~filters.COMMAND,
                    add_receive,
                ),
                CallbackQueryHandler(add_ask_title, pattern="^adm_done_files$"),
            ],
            WAITING_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_save)],
        },
        fallbacks=[
            CommandHandler("cancel", add_cancel_cmd),
            CommandHandler("start", end_on_start),
            CallbackQueryHandler(add_cancel, pattern="^adm_cancel$"),
        ],
        per_user=True,
    )
    app.add_handler(add_conv)
    app.add_handler(CallbackQueryHandler(stale_session, pattern=r"^adm_(done_files|cancel)$"))
    app.add_handler(CallbackQueryHandler(manage_handler, pattern=r"^(adm_manage:|admx:)"))
    app.add_handler(CallbackQueryHandler(radio_handler, pattern=r"^(radio_menu|about_info|cat_|dl:)"))

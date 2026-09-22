"""
🎙️ أرشيف إذاعة ابن حميد | HR Bot
--------------------------------------------------
نسخة مُعاد هيكلتها مع تعزيزات أمنية وتحسينات في الموثوقية.

أهم الفروقات عن النسخة الأصلية:
  • تتبّع صريح لهوية المشرفين المصرّح لهم (بدل الاعتماد الضمني على
    وجود زر الإدارة في الشات) — كل عملية إدارية تتحقق من user_id.
  • قفل (Lock) حول القراءة/التعديل/الحفظ لملف البيانات لمنع تضارب الكتابة.
  • معرّفات عناصر فريدة فعلاً (timestamp + جزء عشوائي) بدل ثانية التوقيت وحدها.
  • تحقق من نجاح المزامنة مع GitHub وتسجيل واضح للأخطاء بدل الفشل الصامت.
  • تقسيم صفحات (Pagination) لقوائم الأقسام الطويلة تفاديًا لحدود تيليجرام.
  • معالجة أخطاء عند إرسال الملفات (file_id تالف/محذوف) دون توقف الحلقة كاملة.
  • تنظيف الاستثناءات الصامتة الخطرة، واستخدام context manager لكل ملف.
"""

import asyncio
import base64
import json
import logging
import os
import threading
import time
import uuid
from typing import Any, Optional

import requests
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("hr_bot")

# ----------------------------------------------------------------------------
# الإعدادات والثوابت
# ----------------------------------------------------------------------------
TOKEN = os.environ.get("TOKEN")
GH_TOKEN = os.environ.get("GH_TOKEN")
GH_REPO = os.environ.get("GH_REPO")

if not TOKEN:
    raise ValueError("❌ خطأ: لم يتم العثور على التوكن! تأكد من ضبط متغير البيئة TOKEN.")

PASSCODE = os.environ.get("PASSCODE")
if not PASSCODE:
    # لا نضع كلمة سر افتراضية مكتوبة في الكود؛ يجب ضبطها كمتغير بيئة.
    raise ValueError("❌ خطأ: لم يتم ضبط متغير البيئة PASSCODE. حدد كلمة سر قوية للإدارة.")

DATA_FILE = "data.json"
ADMIN_SESSIONS_FILE = "admin_sessions.json"
MAX_BATCH_FILES = 10
ITEMS_PER_PAGE = 8  # عدد الأزرار في كل صفحة من قوائم الأقسام

ADMIN_PASSCODE, ADMIN_SELECT_CAT, ADMIN_WAITING_FILES, ADMIN_WAITING_TITLE = range(4)

ABOUT_TEXT = (
    "🎙️ **عن أرشيف إذاعة ابن حميد | HR Bot**\n"
    "━━━━━━━ • 📻 • ━━━━━━━\n\n"
    "منصة رقمية لتوثيق وأرشفة المحتوى الإذاعي وتسهيل الوصول للأنشطة المدرسية.\n\n"
    "👨‍🏫 **الإشراف العام:** رائد النشاط أ.فهد\n"
    "💻 **التطوير والبرمجة:** AboZaid"
)

DEFAULT_DATA = {"broadcasts": {}, "videos": {}, "dialogues": {}, "quizzes": {}, "general_archive": {}}
CAT_NAMES = {
    "broadcasts": "🎙️ قسم الإذاعات",
    "videos": "📺 قسم المقاطع المرئية",
    "dialogues": "🎧 قسم الحوارات الإذاعية",
    "quizzes": "🎁 قسم الأسئلة الإذاعية",
    "general_archive": "🗄️ الأرشيف العام",
}

# قفل عام يحمي أي قراءة/تعديل/كتابة لملف البيانات من التضارب.
DATA_LOCK = threading.Lock()
# قفل مستقل لملف جلسات المشرفين المصرّح لهم.
ADMIN_LOCK = threading.Lock()


# ----------------------------------------------------------------------------
# التعامل مع بيانات الأرشيف
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
    """يُستخدم فقط من داخل قسم محمي بـ DATA_LOCK بالفعل."""
    tmp_path = f"{DATA_FILE}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    os.replace(tmp_path, DATA_FILE)  # كتابة ذرية لتفادي تلف الملف عند انقطاع مفاجئ


def save_data(data: dict) -> None:
    with DATA_LOCK:
        _save_data_locked(data)


def new_item_id() -> str:
    """معرّف فريد عمليًا حتى مع إضافات متزامنة في نفس الثانية."""
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
            logger.error(
                "فشلت مزامنة GitHub (status=%s): %s",
                put_res.status_code,
                put_res.text[:500],
            )
        else:
            logger.info("✅ تمت مزامنة الأرشيف مع GitHub بنجاح.")
    except requests.RequestException as e:
        logger.error("GitHub Network Error: %s", e)
    except Exception as e:
        logger.error("GitHub Error غير متوقع: %s", e)


# ----------------------------------------------------------------------------
# تتبّع جلسات المشرفين المصرّح لهم
# ----------------------------------------------------------------------------
def _load_admin_sessions() -> set:
    with ADMIN_LOCK:
        if not os.path.exists(ADMIN_SESSIONS_FILE):
            return set()
        try:
            with open(ADMIN_SESSIONS_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception as e:
            logger.error("خطأ بقراءة ملف جلسات الإدارة: %s", e)
            return set()


def _save_admin_sessions(sessions: set) -> None:
    with ADMIN_LOCK:
        tmp_path = f"{ADMIN_SESSIONS_FILE}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(list(sessions), f)
        os.replace(tmp_path, ADMIN_SESSIONS_FILE)


def authorize_admin(user_id: int) -> None:
    sessions = _load_admin_sessions()
    sessions.add(user_id)
    _save_admin_sessions(sessions)


def revoke_admin(user_id: int) -> None:
    sessions = _load_admin_sessions()
    sessions.discard(user_id)
    _save_admin_sessions(sessions)


def is_authorized_admin(user_id: Optional[int]) -> bool:
    if user_id is None:
        return False
    return user_id in _load_admin_sessions()


# ----------------------------------------------------------------------------
# خادم HTTP بسيط (لإبقاء الخدمة حية على منصات مثل Render)
# ----------------------------------------------------------------------------
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"HR Radio Archive Bot Running")

    def log_message(self, format, *args):  # noqa: A002 - إسكات لوق HTTP الافتراضي المزعج
        return


def run_dummy_server():
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 10000))), SimpleHandler).serve_forever()


# ----------------------------------------------------------------------------
# أدوات مساعدة عامة
# ----------------------------------------------------------------------------
async def safe_delete_msg(context: ContextTypes.DEFAULT_TYPE, chat_id: int, msg_id: int) -> None:
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except TelegramError:
        pass


async def safe_edit_text(query, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None) -> None:
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    except BadRequest as e:
        # "Message is not modified" شائعة وغير ضارة؛ غيرها نسجّله.
        if "not modified" not in str(e).lower():
            logger.warning("BadRequest عند تعديل رسالة: %s", e)


def build_paginated_keyboard(
    items: list[tuple[str, InlineKeyboardButton]],
    page: int,
    page_prefix: str,
    extra_rows: Optional[list[list[InlineKeyboardButton]]] = None,
) -> InlineKeyboardMarkup:
    """يبني كيبورد مقسّم على صفحات من قائمة أزرار (id, button)."""
    total_pages = max(1, (len(items) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * ITEMS_PER_PAGE
    chunk = items[start:start + ITEMS_PER_PAGE]

    rows = [[btn] for _, btn in chunk]

    if total_pages > 1:
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("◀️ السابق", callback_data=f"{page_prefix}:{page - 1}"))
        nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("التالي ▶️", callback_data=f"{page_prefix}:{page + 1}"))
        rows.append(nav_row)

    if extra_rows:
        rows.extend(extra_rows)

    return InlineKeyboardMarkup(rows)


async def require_admin(update: Update) -> bool:
    """يتحقق أن مُرسل الحدث مشرف مصرّح له حاليًا؛ يرسل تنبيهًا إن لم يكن كذلك."""
    user_id = update.effective_user.id if update.effective_user else None
    if is_authorized_admin(user_id):
        return True
    if update.callback_query:
        await update.callback_query.answer("🚫 صلاحيتك غير مفعّلة. استخدم /admin لتسجيل الدخول.", show_alert=True)
    return False


# ----------------------------------------------------------------------------
# الشاشة الرئيسية للطلاب
# ----------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📁 الأرشيف الإذاعي", callback_data="archive_menu")],
        [InlineKeyboardButton("🗄️ الأرشيف العام (المحتوى المكتمل)", callback_data="cat_general_archive:0")],
        [InlineKeyboardButton("ℹ️ عن الأرشيف", callback_data="about_info")],
    ])
    text = (
        "✨ **أرشيف إذاعة ابن حميد | HR Bot**\n"
        "━━━━━━━ • 📻 • ━━━━━━━\n\n"
        "مرحباً بك في المنصة الرقمية الموحدة لتوثيق المحتوى الإذاعي.\n"
        "اختر من القائمة أدناه:"
    )
    if update.callback_query:
        await safe_edit_text(update.callback_query, text, kb)
    else:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")


# ----------------------------------------------------------------------------
# معالج واجهة الطلاب والأرشيف
# ----------------------------------------------------------------------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query, data = update.callback_query, update.callback_query.data
    await query.answer()

    if data == "noop":
        return

    db = load_data()
    back_main = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main")]])

    if data == "main":
        await start(update, context)

    elif data == "about_info":
        await safe_edit_text(query, ABOUT_TEXT, back_main)

    elif data == "archive_menu":
        kb = [[InlineKeyboardButton(name, callback_data=f"cat_{k}:0")] for k, name in CAT_NAMES.items() if k != "general_archive"]
        kb.append([InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main")])
        await safe_edit_text(
            query,
            "📁 **الأرشيف الإذاعي**\n━━━━━━━ • 📑 • ━━━━━━━\n\nاختر القسم المطلوب:",
            InlineKeyboardMarkup(kb),
        )

    elif data.startswith("cat_"):
        # الصيغة: cat_<key>:<page>
        payload = data[len("cat_"):]
        cat_key, _, page_str = payload.partition(":")
        page = int(page_str) if page_str.isdigit() else 0
        items = db.get(cat_key, {})

        text = f"📂 **{CAT_NAMES.get(cat_key, '')}**\n━━━━━━━ • 📑 • ━━━━━━━\n\n"
        if not items:
            text += "⚠️ **لا توجد ملفات مضافة حالياً.**"
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 رجوع للأرشيف", callback_data="archive_menu")] if cat_key != "general_archive" else [],
                [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main")],
            ])
        else:
            text += "👇 **قائمة المحتوى المتاح:**"
            entries = list(items.items())
            buttons = [
                (item_id, InlineKeyboardButton(f"📥 {idx}. {info['title']}", callback_data=f"dl:{cat_key}:{item_id}"))
                for idx, (item_id, info) in enumerate(entries, start=1)
            ]
            back_row = [InlineKeyboardButton("🔙 رجوع للأرشيف", callback_data="archive_menu")] if cat_key != "general_archive" else []
            extra_rows = [back_row + [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main")]]
            kb = build_paginated_keyboard(buttons, page, f"cat_{cat_key}", extra_rows)

        await safe_edit_text(query, text, kb)

    elif data.startswith("dl:"):
        _, cat_key, item_id = data.split(":", 2)
        item = db.get(cat_key, {}).get(item_id)
        if not item:
            await safe_edit_text(query, "⚠️ لم يعد هذا المحتوى متاحًا.", back_main)
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
            InlineKeyboardButton("🔙 رجوع للقسم", callback_data=f"cat_{cat_key}:0"),
            InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main"),
        ]]
        await context.bot.send_message(chat_id=chat_id, text=footer, reply_markup=InlineKeyboardMarkup(nav), parse_mode="Markdown")


# ----------------------------------------------------------------------------
# لوحة التحكم والإدارة (/admin)
# ----------------------------------------------------------------------------
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    user_id = update.effective_user.id
    if is_authorized_admin(user_id):
        return await show_admin_panel(update, context)

    msg = await update.message.reply_text(
        "🔒 **الإدارة:** أدخل **كلمة السر**:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="adm_cancel_op")]]),
        parse_mode="Markdown",
    )
    context.user_data["last_msg_id"] = msg.message_id
    return ADMIN_PASSCODE


async def admin_verify_passcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    await safe_delete_msg(context, chat_id, update.message.message_id)
    if "last_msg_id" in context.user_data:
        await safe_delete_msg(context, chat_id, context.user_data["last_msg_id"])

    if update.message.text.strip() == PASSCODE:
        authorize_admin(user_id)
        logger.info("تسجيل دخول ناجح للإدارة: user_id=%s", user_id)
        return await show_admin_panel(update, context)

    context.user_data.clear()
    logger.warning("محاولة دخول فاشلة للإدارة: user_id=%s", user_id)
    await context.bot.send_message(chat_id, "❌ كلمة السر غير صحيحة.")
    return ConversationHandler.END


async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إضافة ملفات جديدة", callback_data="adm_add_start")],
        [InlineKeyboardButton("📦 نقل للأرشيف العام", callback_data="adm_move_select_cat")],
        [InlineKeyboardButton("🗑️ حذف ملف من الأقسام", callback_data="adm_del_select_cat")],
        [InlineKeyboardButton("🚪 تسجيل الخروج", callback_data="adm_logout")],
        [InlineKeyboardButton("❌ إغلاق لوحة التحكم", callback_data="adm_close")],
    ])
    text = "⚙️ **لوحة تحكم المشرفين | HR Admin**"
    if update.callback_query:
        await safe_edit_text(update.callback_query, text, kb)
    else:
        msg = await context.bot.send_message(update.effective_chat.id, text, reply_markup=kb, parse_mode="Markdown")
        context.user_data["last_msg_id"] = msg.message_id
    return ConversationHandler.END


async def admin_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await require_admin(update):
        return ConversationHandler.END
    await query.answer()
    kb = [[InlineKeyboardButton(name, callback_data=f"admcat_{k}")] for k, name in CAT_NAMES.items() if k != "general_archive"]
    kb.append([InlineKeyboardButton("« العودة للوحة الإدارة", callback_data="adm_main")])
    await safe_edit_text(query, "📁 **اختر القسم للإضافة:**", InlineKeyboardMarkup(kb))
    return ADMIN_SELECT_CAT


async def admin_select_cat_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await require_admin(update):
        return ConversationHandler.END
    await query.answer()
    context.user_data["admin_target_cat"] = query.data.replace("admcat_", "")
    context.user_data["admin_files_batch"] = []
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ اكتفيت، انتقل للعنوان", callback_data="adm_done_files")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="adm_cancel_op")],
    ])
    await safe_edit_text(query, f"📥 **أرسل الملفات أو النصوص الآن** (حد أقصى {MAX_BATCH_FILES}):", kb)
    return ADMIN_WAITING_FILES


async def admin_receive_files_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return ConversationHandler.END

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
        [InlineKeyboardButton("❌ إلغاء", callback_data="adm_cancel_op")],
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
    if not await require_admin(update):
        return ConversationHandler.END
    await query.answer()

    if not context.user_data.get("admin_files_batch"):
        await safe_edit_text(
            query,
            "❌ لم ترسل أي مواد!",
            InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="adm_cancel_op")]]),
        )
        return ADMIN_WAITING_FILES

    await safe_edit_text(
        query,
        "✍️ أرسل الآن **الاسم/العنوان الظاهر للمجموعة**:",
        InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="adm_cancel_op")]]),
    )
    return ADMIN_WAITING_TITLE


async def admin_save_file_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return ConversationHandler.END

    chat_id = update.effective_chat.id
    title = update.message.text.strip()
    await safe_delete_msg(context, chat_id, update.message.message_id)

    if not title:
        await context.bot.send_message(chat_id, "⚠️ العنوان لا يمكن أن يكون فارغًا، أعد المحاولة:")
        return ADMIN_WAITING_TITLE

    target_cat = context.user_data.get("admin_target_cat")
    batch = context.user_data.get("admin_files_batch")
    if not target_cat or not batch:
        await context.bot.send_message(chat_id, "❌ حدث خطأ: انتهت الجلسة. ابدأ من جديد عبر /admin.")
        context.user_data.clear()
        return ConversationHandler.END

    db = load_data()
    db.setdefault(target_cat, {})[new_item_id()] = {"title": title, "files": batch}
    save_data(db)
    asyncio.create_task(asyncio.to_thread(sync_to_github))
    context.user_data.clear()

    await context.bot.send_message(
        chat_id,
        f"🎉 **تمت الإضافة بنجاح!**\n📌 **العنوان:** `{title}`",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« عودة للإدارة", callback_data="adm_main")]]),
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def admin_del_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يغطي: عرض اللوحة، تسجيل الخروج، الإغلاق، وتدفّقي الحذف/النقل."""
    query, data = update.callback_query, update.callback_query.data

    if data in ("adm_main", "adm_cancel_op"):
        await query.answer()
        context.user_data.clear()
        if not await require_admin(update):
            return ConversationHandler.END
        await show_admin_panel(update, context)
        return ConversationHandler.END

    if not await require_admin(update):
        return ConversationHandler.END

    await query.answer()

    if data == "adm_logout":
        revoke_admin(update.effective_user.id)
        context.user_data.clear()
        await safe_edit_text(query, "🚪 **تم تسجيل الخروج من لوحة الإدارة.**")
        return ConversationHandler.END

    if data == "adm_close":
        context.user_data.clear()
        await safe_delete_msg(context, query.message.chat_id, query.message.message_id)
        return ConversationHandler.END

    op = "del" if "del" in data else "move"

    if data in ("adm_del_select_cat", "adm_move_select_cat"):
        kb = [[InlineKeyboardButton(name, callback_data=f"adm{op}cat_{k}")] for k, name in CAT_NAMES.items()]
        kb.append([InlineKeyboardButton("« عودة للإدارة", callback_data="adm_main")])
        await safe_edit_text(query, f"اختر القسم لـ ({'الحذف' if op == 'del' else 'النقل'}):", InlineKeyboardMarkup(kb))
        return

    if data.startswith(f"adm{op}cat_") and ":" not in data:
        cat_key = data.replace(f"adm{op}cat_", "")
        await _render_admin_item_picker(query, cat_key, op, page=0)
        return

    if data.startswith(f"adm{op}page_"):
        # الصيغة: adm{op}page_<cat_key>:<page>
        payload = data[len(f"adm{op}page_"):]
        cat_key, _, page_str = payload.partition(":")
        await _render_admin_item_picker(query, cat_key, op, page=int(page_str) if page_str.isdigit() else 0)
        return

    if data.startswith(f"admdo{op}:"):
        _, cat_key, iid = data.split(":", 2)
        db = load_data()
        if iid in db.get(cat_key, {}):
            if op == "del":
                del db[cat_key][iid]
            else:
                db.setdefault("general_archive", {})[iid] = db[cat_key].pop(iid)
            save_data(db)
            asyncio.create_task(asyncio.to_thread(sync_to_github))
            await safe_edit_text(
                query,
                "✅ **تمت العملية بنجاح!**",
                InlineKeyboardMarkup([[InlineKeyboardButton("« عودة للإدارة", callback_data="adm_main")]]),
            )
        else:
            await safe_edit_text(
                query,
                "⚠️ هذا العنصر لم يعد موجودًا.",
                InlineKeyboardMarkup([[InlineKeyboardButton("« عودة للإدارة", callback_data="adm_main")]]),
            )


async def _render_admin_item_picker(query, cat_key: str, op: str, page: int) -> None:
    db = load_data()
    items = db.get(cat_key, {})
    if not items:
        await safe_edit_text(
            query, "⚠️ القسم فارغ.",
            InlineKeyboardMarkup([[InlineKeyboardButton("« عودة للإدارة", callback_data="adm_main")]]),
        )
        return

    entries = list(items.items())
    label = "🗑️ حذف" if op == "del" else "📦 نقل"
    buttons = [
        (iid, InlineKeyboardButton(f"{label}: {info['title'][:30]}", callback_data=f"admdo{op}:{cat_key}:{iid}"))
        for iid, info in entries
    ]
    extra_rows = [[InlineKeyboardButton("« عودة للإدارة", callback_data="adm_main")]]
    kb = build_paginated_keyboard(buttons, page, f"adm{op}page_{cat_key}", extra_rows)
    await safe_edit_text(query, "اختر المحتوى المطلوب:", kb)


async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        await safe_edit_text(update.callback_query, "🚫 **تم الإلغاء.**")
    else:
        await update.message.reply_text("🚫 **تم الإلغاء.**")
    context.user_data.clear()
    return ConversationHandler.END


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("حدث استثناء أثناء معالجة تحديث: %s", context.error, exc_info=context.error)


# ----------------------------------------------------------------------------
# تشغيل التطبيق
# ----------------------------------------------------------------------------
def main():
    threading.Thread(target=run_dummy_server, daemon=True).start()
    app = ApplicationBuilder().token(TOKEN).build()

    admin_conv = ConversationHandler(
        entry_points=[
            CommandHandler("admin", admin_command),
            CallbackQueryHandler(admin_add_start, pattern="^adm_add_start$"),
        ],
        states={
            ADMIN_PASSCODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_verify_passcode),
                CallbackQueryHandler(cancel_conv, pattern="^adm_cancel_op$"),
            ],
            ADMIN_SELECT_CAT: [
                CallbackQueryHandler(admin_select_cat_step, pattern="^admcat_"),
                CallbackQueryHandler(admin_del_handler, pattern="^(adm_main|adm_cancel_op)$"),
            ],
            ADMIN_WAITING_FILES: [
                MessageHandler(
                    (filters.Document.ALL | filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE | filters.TEXT)
                    & ~filters.COMMAND,
                    admin_receive_files_step,
                ),
                CallbackQueryHandler(admin_finish_files_step, pattern="^adm_done_files$"),
                CallbackQueryHandler(admin_del_handler, pattern="^adm_cancel_op$"),
            ],
            ADMIN_WAITING_TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_save_file_step),
                CallbackQueryHandler(admin_del_handler, pattern="^adm_cancel_op$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_conv),
            CallbackQueryHandler(admin_del_handler, pattern="^(adm_main|adm_cancel_op)$"),
        ],
        per_user=True,
    )

    app.add_handler(admin_conv)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(admin_del_handler, pattern="^adm"))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_error_handler(error_handler)

    logger.info("✅ HR Bot جاهز للعمل.")
    app.run_polling()


if __name__ == "__main__":
    main()

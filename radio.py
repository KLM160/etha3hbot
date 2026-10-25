"""
📻 الإذاعة المدرسية — أرشيف إذاعة ابن حميد (القسم الأول من اللوحة)
--------------------------------------------------
يحتفظ بنفس بيانات الأرشيف السابق (data.json) ونفس منطقه،
وبدون كلمة سر للإدارة لأن البوت مغلق على المالك بالكامل (انظر owner_guard في main.py).
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

ADMIN_SELECT_CAT, ADMIN_WAITING_FILES, ADMIN_WAITING_TITLE = range(3)

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


# ----------------------------------------------------------------------------
# واجهة الأرشيف
# ----------------------------------------------------------------------------
BACK_RADIO = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 قائمة الإذاعة", callback_data="radio_menu")]])


async def radio_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup(
        grid([
            InlineKeyboardButton("📁 الأرشيف الإذاعي", callback_data="archive_menu"),
            InlineKeyboardButton("🗄️ الأرشيف العام", callback_data="cat_general_archive:0"),
            InlineKeyboardButton("⚙️ إدارة الإذاعة", callback_data="adm_main"),
            InlineKeyboardButton("ℹ️ عن الأرشيف", callback_data="about_info"),
        ]) + [[home_button()]]
    )
    text = (
        "📻 **الإذاعة المدرسية**\n"
        "━━━━━━━ • 📻 • ━━━━━━━\n\n"
        "أرشيف إذاعة ابن حميد لتوثيق المحتوى الإذاعي.\nاختر من القائمة:"
    )
    await safe_edit_text(update.callback_query, text, kb)


async def radio_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query, data = update.callback_query, update.callback_query.data
    await query.answer()

    if data == "radio_menu":
        await radio_menu(update, context)

    elif data == "about_info":
        await safe_edit_text(query, ABOUT_TEXT, BACK_RADIO)

    elif data == "archive_menu":
        kb = grid([InlineKeyboardButton(name, callback_data=f"cat_{k}:0") for k, name in CAT_NAMES.items() if k != "general_archive"])
        kb.append([InlineKeyboardButton("🔙 قائمة الإذاعة", callback_data="radio_menu")])
        await safe_edit_text(
            query,
            "📁 **الأرشيف الإذاعي**\n━━━━━━━ • 📑 • ━━━━━━━\n\nاختر القسم المطلوب:",
            InlineKeyboardMarkup(kb),
        )

    elif data.startswith("cat_"):
        # الصيغة: cat_<key>:<page>
        cat_key, _, page_str = data[len("cat_"):].partition(":")
        page = int(page_str) if page_str.isdigit() else 0
        items = load_data().get(cat_key, {})
        back_row = [InlineKeyboardButton("🔙 رجوع للأرشيف", callback_data="archive_menu")] if cat_key != "general_archive" else []
        home_row = back_row + [InlineKeyboardButton("🔙 قائمة الإذاعة", callback_data="radio_menu")]

        text = f"📂 **{CAT_NAMES.get(cat_key, '')}**\n━━━━━━━ • 📑 • ━━━━━━━\n\n"
        if not items:
            text += "⚠️ **لا توجد ملفات مضافة حالياً.**"
            kb = InlineKeyboardMarkup([home_row])
        else:
            text += "👇 **قائمة المحتوى المتاح:**"
            buttons = [
                (item_id, InlineKeyboardButton(f"📥 {idx}. {info['title']}", callback_data=f"dl:{cat_key}:{item_id}"))
                for idx, (item_id, info) in enumerate(items.items(), start=1)
            ]
            kb = build_paginated_keyboard(buttons, page, f"cat_{cat_key}", [home_row])
        await safe_edit_text(query, text, kb)

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
            InlineKeyboardButton("🔙 رجوع للقسم", callback_data=f"cat_{cat_key}:0"),
            InlineKeyboardButton("📻 قائمة الإذاعة", callback_data="radio_menu"),
        ]]
        await context.bot.send_message(chat_id=chat_id, text=footer, reply_markup=InlineKeyboardMarkup(nav), parse_mode="Markdown")


# ----------------------------------------------------------------------------
# إدارة الإذاعة (/admin) — بدون كلمة سر، الحماية عبر owner_guard
# ----------------------------------------------------------------------------
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    return await show_admin_panel(update, context)


async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup(
        grid([
            InlineKeyboardButton("➕ إضافة ملفات", callback_data="adm_add_start"),
            InlineKeyboardButton("📦 نقل للأرشيف العام", callback_data="adm_move_select_cat"),
            InlineKeyboardButton("🗑️ حذف ملف", callback_data="adm_del_select_cat"),
            InlineKeyboardButton("🔙 قائمة الإذاعة", callback_data="radio_menu"),
        ]) + [[InlineKeyboardButton("❌ إغلاق لوحة التحكم", callback_data="adm_close")]]
    )
    text = "⚙️ **إدارة الإذاعة المدرسية**"
    if update.callback_query:
        await safe_edit_text(update.callback_query, text, kb)
    else:
        await context.bot.send_message(update.effective_chat.id, text, reply_markup=kb, parse_mode="Markdown")
    return ConversationHandler.END


async def admin_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kb = grid([InlineKeyboardButton(name, callback_data=f"admcat_{k}") for k, name in CAT_NAMES.items() if k != "general_archive"])
    kb.append([InlineKeyboardButton("« العودة للوحة الإدارة", callback_data="adm_main")])
    await safe_edit_text(query, "📁 **اختر القسم للإضافة:**", InlineKeyboardMarkup(kb))
    return ADMIN_SELECT_CAT


async def admin_select_cat_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
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
    await query.answer()
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="adm_cancel_op")]])

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
    """يغطي: عرض اللوحة، الإغلاق، وتدفّقي الحذف/النقل."""
    query, data = update.callback_query, update.callback_query.data
    await query.answer()

    if data in ("adm_main", "adm_cancel_op"):
        context.user_data.clear()
        await show_admin_panel(update, context)
        return ConversationHandler.END

    if data == "adm_close":
        context.user_data.clear()
        await safe_delete_msg(context, query.message.chat_id, query.message.message_id)
        return ConversationHandler.END

    op = "del" if "del" in data else "move"
    back_admin = InlineKeyboardMarkup([[InlineKeyboardButton("« عودة للإدارة", callback_data="adm_main")]])

    if data in ("adm_del_select_cat", "adm_move_select_cat"):
        kb = grid([InlineKeyboardButton(name, callback_data=f"adm{op}cat_{k}") for k, name in CAT_NAMES.items()])
        kb.append([InlineKeyboardButton("« عودة للإدارة", callback_data="adm_main")])
        await safe_edit_text(query, f"اختر القسم لـ ({'الحذف' if op == 'del' else 'النقل'}):", InlineKeyboardMarkup(kb))
        return

    if data.startswith(f"adm{op}cat_") and ":" not in data:
        await _render_admin_item_picker(query, data.replace(f"adm{op}cat_", ""), op, page=0)
        return

    if data.startswith(f"adm{op}page_"):
        # الصيغة: adm{op}page_<cat_key>:<page>
        cat_key, _, page_str = data[len(f"adm{op}page_"):].partition(":")
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
            await safe_edit_text(query, "✅ **تمت العملية بنجاح!**", back_admin)
        else:
            await safe_edit_text(query, "⚠️ هذا العنصر لم يعد موجودًا.", back_admin)


async def _render_admin_item_picker(query, cat_key: str, op: str, page: int) -> None:
    items = load_data().get(cat_key, {})
    if not items:
        await safe_edit_text(
            query, "⚠️ القسم فارغ.",
            InlineKeyboardMarkup([[InlineKeyboardButton("« عودة للإدارة", callback_data="adm_main")]]),
        )
        return

    label = "🗑️ حذف" if op == "del" else "📦 نقل"
    buttons = [
        (iid, InlineKeyboardButton(f"{label}: {info['title'][:30]}", callback_data=f"admdo{op}:{cat_key}:{iid}"))
        for iid, info in items.items()
    ]
    extra_rows = [[InlineKeyboardButton("« عودة للإدارة", callback_data="adm_main")]]
    await safe_edit_text(query, "اختر المحتوى المطلوب:", build_paginated_keyboard(buttons, page, f"adm{op}page_{cat_key}", extra_rows))


async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 **تم الإلغاء.**", parse_mode="Markdown")
    context.user_data.clear()
    return ConversationHandler.END


# ----------------------------------------------------------------------------
# الربط مع main.py
# ----------------------------------------------------------------------------
def register(app) -> None:
    admin_conv = ConversationHandler(
        entry_points=[
            CommandHandler("admin", admin_command),
            CallbackQueryHandler(admin_add_start, pattern="^adm_add_start$"),
        ],
        states={
            ADMIN_SELECT_CAT: [
                CallbackQueryHandler(admin_select_cat_step, pattern="^admcat_"),
            ],
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
            CallbackQueryHandler(admin_del_handler, pattern="^(adm_main|adm_cancel_op)$"),
        ],
        per_user=True,
    )
    app.add_handler(admin_conv)
    app.add_handler(CallbackQueryHandler(admin_del_handler, pattern="^adm"))
    app.add_handler(CallbackQueryHandler(radio_handler, pattern=r"^(radio_menu|archive_menu|about_info|cat_|dl:)"))

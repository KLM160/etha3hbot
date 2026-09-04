import os
import json
import logging
import threading
import base64
import asyncio
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)

# إعداد السجل
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

TOKEN = os.environ.get("TOKEN")
GH_TOKEN = os.environ.get("GH_TOKEN")
GH_REPO = os.environ.get("GH_REPO")

if not TOKEN:
    raise ValueError("❌ خطأ: لم يتم العثور على التوكن!")

PASSCODE = os.environ.get("PASSCODE", "ibnhomaid11")
DATA_FILE = "data.json"
MAX_BATCH_FILES = 10

ADMIN_PASSCODE, ADMIN_SELECT_CAT, ADMIN_WAITING_FILES, ADMIN_WAITING_TITLE = range(4)

ABOUT_TEXT = (
    "🎙️ **عن أرشيف إذاعة ابن حميد | HR Bot**\n"
    "━━━━━━━ • 📻 • ━━━━━━━\n\n"
    "منصة رقمية مخصصة لتوثيق وأرشفة المحتوى الإذاعي والأنشطة الطلابية، "
    "تهدف إلى تسهيل الوصول للإذاعات المدرسية، المقاطع المرئية، الحوارات، "
    "والأسئلة الثقافية وبنائها وفق أحدث معايير التنظيم الرقمي.\n\n"
    "👨‍🏫 **الإشراف العام:** أ. أبو باسل (رائد النشاط الطلابي)\n"
    "💻 **التطوير والبرمجة:** AboZaid\n\n"
    "✨ *هذا العمل خدمة للنشاط الطلابي والمجتمع المدرسي.*"
)

DEFAULT_DATA = {"broadcasts": {}, "videos": {}, "dialogues": {}, "quizzes": {}}

# ------------------------------------------
# 📂 إدارة البيانات و GitHub
# ------------------------------------------
def load_data():
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_DATA, f, ensure_ascii=False, indent=4)
        return DEFAULT_DATA
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            for cat in DEFAULT_DATA:
                if cat not in data:
                    data[cat] = {}
            return data
    except Exception as e:
        logging.error(f"Error loading JSON: {e}")
        return DEFAULT_DATA

def save_data_instant(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def sync_to_github():
    if not GH_TOKEN or not GH_REPO:
        return
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{DATA_FILE}"
    headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            content = f.read()
        encoded_content = base64.b64encode(content.encode("utf-8")).decode("utf-8")

        sha = None
        get_res = requests.get(url, headers=headers)
        if get_res.status_code == 200:
            sha = get_res.json().get("sha")

        payload = {"message": "🔄 التحديث التلقائي لأرشيف الإذاعة", "content": encoded_content}
        if sha:
            payload["sha"] = sha
        requests.put(url, headers=headers, json=payload)
    except Exception as e:
        logging.error(f"GitHub Sync Error: {e}")

async def delayed_github_sync():
    await asyncio.sleep(3)
    sync_to_github()

# ------------------------------------------
# 🌐 السيرفر الوهمي
# ------------------------------------------
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"HR Radio Archive Bot Running")

def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), SimpleHandler).serve_forever()

# ------------------------------------------
# 🧹 دوال مساعدة آمنة
# ------------------------------------------
async def safe_delete_msg(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass

async def safe_edit_text(query, text, reply_markup=None):
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    except BadRequest:
        pass

# ------------------------------------------
# 🏠 الشاشة الرئيسية والأقسام
# ------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    keyboard = [
        [InlineKeyboardButton("📁 الأرشيف الإذاعي", callback_data="archive_menu")],
        [InlineKeyboardButton("ℹ️ عن الأرشيف", callback_data="about_info")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = (
        "✨ **أرشيف إذاعة ابن حميد | HR Bot**\n"
        "━━━━━━━ • 📻 • ━━━━━━━\n\n"
        "مرحباً بك في المنصة الرقمية الموحدة لإدارة وتوثيق المحتوى الإذاعي.\n\n"
        "اختر أحد الخيارات التالية للاستعراض والتصفح:"
    )
    if update.callback_query:
        await safe_edit_text(update.callback_query, text, reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    db = load_data()
    back_main = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main")]])

    if data == "main":
        await start(update, context)

    elif data == "about_info":
        await safe_edit_text(query, ABOUT_TEXT, back_main)

    elif data == "archive_menu":
        keyboard = [
            [InlineKeyboardButton("🎙️ قسم الإذاعات المدرسية", callback_data="cat_broadcasts")],
            [InlineKeyboardButton("📺 قسم المقاطع المرئية", callback_data="cat_videos")],
            [InlineKeyboardButton("🎧 قسم الحوارات الإذاعية", callback_data="cat_dialogues")],
            [InlineKeyboardButton("🎁 قسم الأسئلة الإذاعية", callback_data="cat_quizzes")],
            [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main")]
        ]
        text = "📁 **الأرشيف الإذاعي**\n━━━━━━━ • 📑 • ━━━━━━━\n\nاختر القسم المطلوب للوصول إلى الملفات:"
        await safe_edit_text(query, text, InlineKeyboardMarkup(keyboard))

    elif data.startswith("cat_"):
        cat_key = data.replace("cat_", "")
        items = db.get(cat_key, {})
        cat_names = {
            "broadcasts": "🎙️ قسم الإذاعات المدرسية",
            "videos": "📺 قسم المقاطع المرئية",
            "dialogues": "🎧 قسم الحوارات الإذاعية",
            "quizzes": "🎁 قسم الأسئلة الإذاعية"
        }
        keyboard = [[InlineKeyboardButton(f"📄 {info['title']}", callback_data=f"dl:{cat_key}:{item_id}")] for item_id, info in items.items()]
        keyboard.append([InlineKeyboardButton("🔙 رجوع للأرشيف", callback_data="archive_menu"), InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main")])

        text = f"📂 **{cat_names.get(cat_key, '')}**\n━━━━━━━ • 📑 • ━━━━━━━\n\n"
        text += "👇 **اختر الملف الذي تريده:**" if items else "⚠️ **لا توجد ملفات مضافة حالياً.**"
        await safe_edit_text(query, text, InlineKeyboardMarkup(keyboard))

    elif data.startswith("dl:"):
        parts = data.split(":", 2)
        if len(parts) == 3:
            _, cat_key, item_id = parts
            item = db.get(cat_key, {}).get(item_id)
            if item:
                await safe_edit_text(query, "⏳ **جاري جلب المحتوى...**")
                chat_id = query.message.chat_id
                file_list = item.get("files", [])

                for idx, f in enumerate(file_list):
                    f_id, f_type, f_text = f.get("file_id"), f.get("type"), f.get("text")
                    if f_type == "text":
                        await context.bot.send_message(chat_id=chat_id, text=f"📌 **{item['title']}**\n\n{f_text}", parse_mode="Markdown")
                    elif f_type == "video":
                        await context.bot.send_video(chat_id=chat_id, video=f_id, caption=f"🎥 **{item['title']}**", parse_mode="Markdown")
                    elif f_type == "photo":
                        await context.bot.send_photo(chat_id=chat_id, photo=f_id, caption=f"🖼️ **{item['title']}**", parse_mode="Markdown")
                    elif f_type == "audio":
                        await context.bot.send_audio(chat_id=chat_id, audio=f_id, caption=f"🎵 **{item['title']}**", parse_mode="Markdown")
                    else:
                        await context.bot.send_document(chat_id=chat_id, document=f_id, caption=f"📄 **{item['title']}**", parse_mode="Markdown")

                    if idx < len(file_list) - 1:
                        await asyncio.sleep(0.3)

                await safe_delete_msg(context, chat_id, query.message.message_id)
                nav_kb = [[InlineKeyboardButton("🔙 رجوع للقسم", callback_data=f"cat_{cat_key}"), InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main")]]
                await context.bot.send_message(chat_id=chat_id, text="✨ تم الإرسال بنجاح.", reply_markup=InlineKeyboardMarkup(nav_kb))
            else:
                await safe_edit_text(query, "❌ لم يعد هذا المحتوى متوفراً.", back_main)

# ------------------------------------------
# ⚙️ نظام الإدارة /admin
# ------------------------------------------
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    msg = await update.message.reply_text(
        "🔒 **منطقة الإدارة والتحكم:**\nيرجى إدخال **كلمة السر المعتمدة**:",
        parse_mode="Markdown"
    )
    context.user_data["last_msg_id"] = msg.message_id
    return ADMIN_PASSCODE

async def admin_verify_passcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_input = update.message.text.strip()
    await safe_delete_msg(context, chat_id, update.message.message_id)
    if "last_msg_id" in context.user_data:
        await safe_delete_msg(context, chat_id, context.user_data["last_msg_id"])

    if user_input == PASSCODE:
        return await show_admin_panel(update, context)
    else:
        context.user_data.clear()
        await context.bot.send_message(chat_id=chat_id, text="❌ كلمة السر غير صحيحة.")
        return ConversationHandler.END

async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("➕ إضافة ملفات جديدة", callback_data="adm_add_start")],
        [InlineKeyboardButton("🗑️ حذف ملف من الأقسام", callback_data="adm_del_select_cat")],
        [InlineKeyboardButton("❌ إغلاق لوحة التحكم", callback_data="adm_close")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "⚙️ **لوحة تحكم المشرفين | HR Admin**\n━━━━━━━ • 🛠️ • ━━━━━━━\n\nاختر الإجراء المطلوب أدناه:"

    chat_id = update.effective_chat.id
    if update.callback_query:
        await safe_edit_text(update.callback_query, text, reply_markup)
    else:
        msg = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode="Markdown")
        context.user_data["last_msg_id"] = msg.message_id
    return ConversationHandler.END

async def admin_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("🎙️ قسم الإذاعات", callback_data="admcat_broadcasts")],
        [InlineKeyboardButton("📺 قسم المقاطع المرئية", callback_data="admcat_videos")],
        [InlineKeyboardButton("🎧 قسم الحوارات الإذاعية", callback_data="admcat_dialogues")],
        [InlineKeyboardButton("🎁 قسم الأسئلة الإذاعية", callback_data="admcat_quizzes")],
        [InlineKeyboardButton("« العودة للوحة الإدارة", callback_data="adm_main")]
    ]
    await safe_edit_text(query, "📁 **اختر القسم الذي تريد إضافة الملفات إليه:**", InlineKeyboardMarkup(keyboard))
    return ADMIN_SELECT_CAT

async def admin_select_cat_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["admin_target_cat"] = query.data.replace("admcat_", "")
    context.user_data["admin_files_batch"] = []

    keyboard = [
        [InlineKeyboardButton("✅ اكتفيت بالمحتوى، انتقل لتحديد العنوان", callback_data="adm_done_files")],
        [InlineKeyboardButton("❌ إلغاء والعودة للإدارة", callback_data="adm_cancel_op")]
    ]
    await safe_edit_text(query, f"📥 **أرسل الملفات أو النصوص الآن** (حد أقصى {MAX_BATCH_FILES}):\nعند الانتهاء اضغط الزر أدناه:", InlineKeyboardMarkup(keyboard))
    context.user_data["prompt_msg_id"] = query.message.message_id
    return ADMIN_WAITING_FILES

async def admin_receive_files_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    batch = context.user_data.get("admin_files_batch", [])

    if len(batch) >= MAX_BATCH_FILES:
        await update.message.reply_text(f"⚠️ وصلت للحد الأقصى ({MAX_BATCH_FILES} ملفات)!")
        return ADMIN_WAITING_FILES

    file_id, file_type, text_content = None, "document", None

    if update.message.document:
        file_id, file_type = update.message.document.file_id, "document"
    elif update.message.video:
        file_id, file_type = update.message.video.file_id, "video"
    elif update.message.photo:
        file_id, file_type = update.message.photo[-1].file_id, "photo"
    elif update.message.audio or update.message.voice:
        file_id = (update.message.audio or update.message.voice).file_id
        file_type = "audio"
    elif update.message.text:
        text_content, file_type = update.message.text, "text"

    if "prompt_msg_id" in context.user_data:
        await safe_delete_msg(context, chat_id, context.user_data["prompt_msg_id"])

    batch.append({"file_id": file_id, "type": file_type, "text": text_content})
    context.user_data["admin_files_batch"] = batch

    keyboard = [
        [InlineKeyboardButton("✅ اكتفيت بالمحتوى، انتقل لتحديد العنوان", callback_data="adm_done_files")],
        [InlineKeyboardButton("❌ إلغاء والعودة للإدارة", callback_data="adm_cancel_op")]
    ]
    new_msg = await update.message.reply_text(
        f"📥 **تم استلام المادة رقم ({len(batch)}/{MAX_BATCH_FILES}) بنجاح!**\nأرسل المادة التالية أو اضغط الزر:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    context.user_data["prompt_msg_id"] = new_msg.message_id
    return ADMIN_WAITING_FILES

async def admin_finish_files_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    files = context.user_data.get("admin_files_batch", [])
    if not files:
        await safe_edit_text(query, "❌ لم يتم إرسال أي مواد بعد!", InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="adm_cancel_op")]]))
        return ADMIN_WAITING_FILES

    await safe_edit_text(
        query,
        f"✍️ تم جمع `{len(files)}` مواد.\nأرسل الآن **الاسم/العنوان الظاهر للمجموعة** للطلاب:",
        InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="adm_cancel_op")]])
    )
    context.user_data["prompt_msg_id"] = query.message.message_id
    return ADMIN_WAITING_TITLE

async def admin_save_file_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    title = update.message.text.strip()
    target_cat = context.user_data.get("admin_target_cat")
    files = context.user_data.get("admin_files_batch", [])

    await safe_delete_msg(context, chat_id, update.message.message_id)
    if "prompt_msg_id" in context.user_data:
        await safe_delete_msg(context, chat_id, context.user_data["prompt_msg_id"])

    item_key = f"item_{int(update.message.date.timestamp())}"
    db = load_data()
    db[target_cat][item_key] = {"title": title, "files": files}

    save_data_instant(db)
    asyncio.create_task(delayed_github_sync())
    context.user_data.clear()

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🎉 **تمت إضافة المحتوى وتوفيره فوراً!**\n📌 **العنوان:** `{title}`\n📦 **عدد العناصر:** `{len(files)}`",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« العودة للوحة الإدارة", callback_data="adm_main")]]),
        parse_mode="Markdown"
    )
    return ConversationHandler.END

# ------------------------------------------
# 🗑️ الحذف والتحكم
# ------------------------------------------
async def admin_del_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()
    db = load_data()

    if data in ["adm_main", "adm_cancel_op"]:
        context.user_data.clear()
        await show_admin_panel(update, context)
        return

    if data == "adm_close":
        context.user_data.clear()
        await safe_delete_msg(context, query.message.chat_id, query.message.message_id)
        return

    if data == "adm_del_select_cat":
        keyboard = [
            [InlineKeyboardButton("🎙️ قسم الإذاعات", callback_data="admdelcat_broadcasts")],
            [InlineKeyboardButton("📺 قسم المقاطع المرئية", callback_data="admdelcat_videos")],
            [InlineKeyboardButton("🎧 قسم الحوارات الإذاعية", callback_data="admdelcat_dialogues")],
            [InlineKeyboardButton("🎁 قسم الأسئلة الإذاعية", callback_data="admdelcat_quizzes")],
            [InlineKeyboardButton("« العودة للوحة الإدارة", callback_data="adm_main")]
        ]
        await safe_edit_text(query, "🗑️ **اختر القسم الذي تريد حذف ملف منه:**", InlineKeyboardMarkup(keyboard))

    elif data.startswith("admdelcat_"):
        cat_key = data.replace("admdelcat_", "")
        items = db.get(cat_key, {})
        if not items:
            await safe_edit_text(query, "⚠️ **لا توجد ملفات في هذا القسم لحذفها.**", InlineKeyboardMarkup([[InlineKeyboardButton("« العودة للوحة الإدارة", callback_data="adm_main")]]))
            return

        keyboard = [[InlineKeyboardButton(f"🗑️ {info['title']}", callback_data=f"admdodell:{cat_key}:{item_id}")] for item_id, info in items.items()]
        keyboard.append([InlineKeyboardButton("« العودة للوحة الإدارة", callback_data="adm_main")])
        await safe_edit_text(query, "اختر الملف المراد حذفه نهائياً:", InlineKeyboardMarkup(keyboard))

    elif data.startswith("admdodell:"):
        parts = data.split(":", 2)
        if len(parts) == 3:
            _, cat_key, item_id = parts
            if item_id in db.get(cat_key, {}):
                del db[cat_key][item_id]
                save_data_instant(db)
                asyncio.create_task(delayed_github_sync())
                await safe_edit_text(query, "✅ **تم حذف الملف وحفظ التغييرات فوراً!**", InlineKeyboardMarkup([[InlineKeyboardButton("« العودة للوحة الإدارة", callback_data="adm_main")]]))
                return
        await safe_edit_text(query, "❌ لم يتم العثور على الملف.", InlineKeyboardMarkup([[InlineKeyboardButton("« العودة للوحة الإدارة", callback_data="adm_main")]]))

async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("تم إلغاء العملية.")
    return ConversationHandler.END

# ------------------------------------------
# 🚀 تشغيل البوت
# ------------------------------------------
def main():
    threading.Thread(target=run_dummy_server, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).build()

    admin_conv = ConversationHandler(
        entry_points=[
            CommandHandler("admin", admin_command),
            CallbackQueryHandler(admin_add_start, pattern="^adm_add_start$")
        ],
        states={
            ADMIN_PASSCODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_verify_passcode)],
            ADMIN_SELECT_CAT: [
                CallbackQueryHandler(admin_select_cat_step, pattern="^admcat_"),
                CallbackQueryHandler(admin_del_handler, pattern="^(adm_main|adm_cancel_op)$")
            ],
            ADMIN_WAITING_FILES: [
                MessageHandler((filters.Document.ALL | filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE | filters.TEXT) & ~filters.COMMAND, admin_receive_files_step),
                CallbackQueryHandler(admin_finish_files_step, pattern="^adm_done_files$"),
                CallbackQueryHandler(admin_del_handler, pattern="^adm_cancel_op$")
            ],
            ADMIN_WAITING_TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_save_file_step),
                CallbackQueryHandler(admin_del_handler, pattern="^adm_cancel_op$")
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_conv),
            CommandHandler("admin", admin_command),
            CallbackQueryHandler(admin_del_handler, pattern="^(adm_main|adm_cancel_op)$")
        ],
        per_user=True
    )

    app.add_handler(admin_conv)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(admin_del_handler, pattern="^adm_"))
    app.add_handler(CallbackQueryHandler(admin_del_handler, pattern="^admdelcat_"))
    app.add_handler(CallbackQueryHandler(admin_del_handler, pattern="^admdodell:"))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("✅ HR Bot خفيف، سريع، ومحمي من التعليق!")
    app.run_polling()

if __name__ == "__main__":
    main()

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
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

TOKEN = os.environ.get("TOKEN")
GH_TOKEN = os.environ.get("GH_TOKEN")
GH_REPO = os.environ.get("GH_REPO")

if not TOKEN:
    raise ValueError("❌ خطأ: لم يتم العثور على التوكن!")

# 🔒 كلمة السر المعتمدة للإدارة
PASSCODE = os.environ.get("PASSCODE", "ibnhomaid11")

DATA_FILE = "data.json"
MAX_BATCH_FILES = 10  # الحد الأقصى للملفات في الدفعة الواحدة

# حالات المحادثة للإدارة
ADMIN_PASSCODE, ADMIN_SELECT_CAT, ADMIN_WAITING_FILES, ADMIN_WAITING_TITLE = range(4)

# نص "عن الأرشيف" الثابت
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

DEFAULT_DATA = {
    "broadcasts": {},
    "videos": {},
    "dialogues": {},
    "quizzes": {}
}

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
        print(f"Error loading JSON: {e}")
        return DEFAULT_DATA

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    sync_to_github()

def sync_to_github():
    if not GH_TOKEN or not GH_REPO:
        return

    url = f"https://api.github.com/repos/{GH_REPO}/contents/{DATA_FILE}"
    headers = {
        "Authorization": f"token {GH_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            content = f.read()

        encoded_content = base64.b64encode(content.encode("utf-8")).decode("utf-8")

        sha = None
        get_res = requests.get(url, headers=headers)
        if get_res.status_code == 200:
            sha = get_res.json().get("sha")

        payload = {
            "message": "🔄 التحديث التلقائي لأرشيف الإذاعة عبر Telegram Bot",
            "content": encoded_content,
        }
        if sha:
            payload["sha"] = sha

        requests.put(url, headers=headers, json=payload)
    except Exception as e:
        print(f"GitHub Sync Error: {e}")

# ------------------------------------------
# 🌐 السيرفر الوهمي منصة Render
# ------------------------------------------
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"HR Radio Archive Bot is running perfectly!")

def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHandler)
    server.serve_forever()

# ------------------------------------------
# 🧹 دالة مساعدة معالجة وحذف الرسائل بآمان
# ------------------------------------------
async def safe_delete_msg(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except BadRequest:
        pass
    except Exception as e:
        logging.warning(f"Failed to delete msg {message_id}: {e}")

# ------------------------------------------
# 🏠 الشاشة الرئيسية العامة
# ------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    keyboard = [
        [InlineKeyboardButton("📁 الأرشيف الإذاعي", callback_data="archive_menu")],
        [InlineKeyboardButton("ℹ️ عن الأرشيف", callback_data="about_info")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    welcome_text = (
        "✨ **أرشيف إذاعة ابن حميد | HR Bot**\n"
        "━━━━━━━ • 📻 • ━━━━━━━\n\n"
        "مرحباً بك في المنصة الرقمية الموحدة لإدارة وتوثيق المحتوى الإذاعي.\n\n"
        "اختر أحد الخيارات التالية للاستعراض والتصفح:"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")

# ------------------------------------------
# 🔘 معالج تصفح المستخدمين والأقسام
# ------------------------------------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    db = load_data()
    back_to_main_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main")]])

    if data == "about_info":
        await query.edit_message_text(ABOUT_TEXT, reply_markup=back_to_main_markup, parse_mode="Markdown")

    elif data == "archive_menu":
        keyboard = [
            [InlineKeyboardButton("🎙️ قسم الإذاعات المدرسية", callback_data="cat_broadcasts")],
            [InlineKeyboardButton("📺 قسم المقاطع المرئية", callback_data="cat_videos")],
            [InlineKeyboardButton("🎧 قسم الحوارات الإذاعية", callback_data="cat_dialogues")],
            [InlineKeyboardButton("🎁 قسم الأسئلة الإذاعية", callback_data="cat_quizzes")],
            [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main")]
        ]
        text = (
            "📁 **الأرشيف الإذاعي**\n"
            "━━━━━━━ • 📑 • ━━━━━━━\n\n"
            "اختر القسم المطلوب للوصول إلى الملفات:"
        )
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("cat_"):
        cat_key = data.replace("cat_", "")
        items = db.get(cat_key, {})

        cat_names = {
            "broadcasts": "🎙️ قسم الإذاعات المدرسية",
            "videos": "📺 قسم المقاطع المرئية",
            "dialogues": "🎧 قسم الحوارات الإذاعية",
            "quizzes": "🎁 قسم الأسئلة الإذاعية"
        }

        keyboard = []
        for item_id, item_info in items.items():
            keyboard.append([InlineKeyboardButton(f"📄 {item_info['title']}", callback_data=f"dl:{cat_key}:{item_id}")])

        keyboard.append([InlineKeyboardButton("🔙 رجوع للأرشيف", callback_data="archive_menu"), InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main")])

        if not items:
            text = (
                f"📂 **{cat_names.get(cat_key, '')}**\n"
                f"━━━━━━━ • 📑 • ━━━━━━━\n\n"
                f"⚠️ **لا توجد ملفات مضافة في هذا القسم حالياً.**"
            )
        else:
            text = (
                f"📂 **{cat_names.get(cat_key, '')}**\n"
                f"━━━━━━━ • 📑 • ━━━━━━━\n\n"
                f"👇 **اختر الملف الذي تريده من القائمة أدناه ليتم إرساله لك فوراً:**"
            )

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("dl:"):
        parts = data.split(":", 2)
        if len(parts) == 3:
            _, cat_key, item_id = parts
            item = db.get(cat_key, {}).get(item_id)

            if item:
                await query.edit_message_text("⏳ **جاري جلب المحتوى من سيرفرات التليجرام...**", parse_mode="Markdown")
                chat_id = query.message.chat_id

                file_list = item.get("files", [])
                if not file_list and "file_id" in item:
                    file_list = [{"file_id": item.get("file_id"), "type": item.get("type", "document"), "text": item.get("text")}]

                for idx, f in enumerate(file_list):
                    f_id = f.get("file_id")
                    f_type = f.get("type")
                    f_text = f.get("text")

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
                        await asyncio.sleep(0.5)

                await query.delete_message()
                nav_keyboard = [
                    [InlineKeyboardButton("🔙 رجوع للقسم", callback_data=f"cat_{cat_key}"), InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main")]
                ]
                await context.bot.send_message(chat_id=chat_id, text="✨ تم الإرسال بنجاح.", reply_markup=InlineKeyboardMarkup(nav_keyboard))
            else:
                await query.edit_message_text("❌ لم يعد هذا المحتوى متوفراً.", reply_markup=back_to_main_markup)

    elif data == "main":
        await start(update, context)

# ------------------------------------------
# ⚙️ نظام التحكم الإداري /admin
# ------------------------------------------
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    msg = await update.message.reply_text(
        "🔒 **منطقة الإدارة والتحكم (HR Admin):**\n"
        "━━━━━━━ • 🔑 • ━━━━━━━\n\n"
        "يرجى إدخال **كلمة السر المعتمدة** للوصول للوحة التحكم:",
        parse_mode="Markdown"
    )
    context.user_data["last_admin_msg_id"] = msg.message_id
    return ADMIN_PASSCODE

async def admin_verify_passcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_input = update.message.text.strip()

    await safe_delete_msg(context, chat_id, update.message.message_id)
    if "last_admin_msg_id" in context.user_data:
        await safe_delete_msg(context, chat_id, context.user_data["last_admin_msg_id"])

    if user_input == PASSCODE:
        return await show_admin_panel(update, context)
    else:
        context.user_data.clear()
        await context.bot.send_message(chat_id=chat_id, text="❌ **كلمة السر غير صحيحة.** تم إلغاء الوصول للوحة الإدارة.")
        return ConversationHandler.END

async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("➕ إضافة ملفات جديدة", callback_data="adm_add_start")],
        [InlineKeyboardButton("🗑️ حذف ملف من الأقسام", callback_data="adm_del_select_cat")],
        [InlineKeyboardButton("❌ إغلاق لوحة التحكم", callback_data="adm_close")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        "⚙️ **لوحة تحكم المشرفين | HR Admin Panel**\n"
        "━━━━━━━ • 🛠️ • ━━━━━━━\n\n"
        "أهلاً بك! اختر الإجراء المطلوب أدناه:"
    )

    chat_id = update.effective_chat.id
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
        context.user_data["last_admin_msg_id"] = update.callback_query.message.message_id
    else:
        msg = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode="Markdown")
        context.user_data["last_admin_msg_id"] = msg.message_id
    
    return ConversationHandler.END

# ------------------------------------------
# ➕ إضافة المحتوى مع حماية العدد المسموح
# ------------------------------------------
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
    await query.edit_message_text(
        "📁 **اختر القسم الذي تريد إضافة الملفات إليه:**",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    context.user_data["last_admin_msg_id"] = query.message.message_id
    return ADMIN_SELECT_CAT

async def admin_select_cat_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    selected_cat = query.data.replace("admcat_", "")
    context.user_data["admin_target_cat"] = selected_cat
    context.user_data["admin_files_batch"] = []

    keyboard = [
        [InlineKeyboardButton("✅ اكتفيت بالمحتوى، انتقل لتحديد العنوان", callback_data="adm_done_files")],
        [InlineKeyboardButton("❌ إلغاء والعودة للإدارة", callback_data="adm_cancel_op")]
    ]

    await query.edit_message_text(
        f"📥 **قم بأرسال الملفات أو النصوص الآن** (حد أقصى {MAX_BATCH_FILES} ملفات):\n"
        "(يمكنك إرسال مستندات، مقاطع، صور، أو نصوص مباشرة)\n\n"
        "عند الانتهاء، اضغط الزر أدناه لتحديد العنوان:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    context.user_data["prompt_msg_id"] = query.message.message_id
    return ADMIN_WAITING_FILES

async def admin_receive_files_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    if "admin_files_batch" not in context.user_data:
        context.user_data["admin_files_batch"] = []

    if len(context.user_data["admin_files_batch"]) >= MAX_BATCH_FILES:
        await update.message.reply_text(f"⚠️ **وصلت للحد الأقصى ({MAX_BATCH_FILES} ملفات)!** اضغط زر الاكتفاء للبدء في تسمية المجموعة.")
        return ADMIN_WAITING_FILES

    file_id = None
    file_type = "document"
    text_content = None

    if update.message.document:
        file_id = update.message.document.file_id
        file_type = "document"
    elif update.message.video:
        file_id = update.message.video.file_id
        file_type = "video"
    elif update.message.photo:
        file_id = update.message.photo[-1].file_id
        file_type = "photo"
    elif update.message.audio or update.message.voice:
        file_id = (update.message.audio or update.message.voice).file_id
        file_type = "audio"
    elif update.message.text:
        text_content = update.message.text
        file_type = "text"

    if "prompt_msg_id" in context.user_data:
        await safe_delete_msg(context, chat_id, context.user_data["prompt_msg_id"])

    context.user_data["admin_files_batch"].append({
        "file_id": file_id,
        "type": file_type,
        "text": text_content
    })

    keyboard = [
        [InlineKeyboardButton("✅ اكتفيت بالمحتوى، انتقل لتحديد العنوان", callback_data="adm_done_files")],
        [InlineKeyboardButton("❌ إلغاء والعودة للإدارة", callback_data="adm_cancel_op")]
    ]
    count = len(context.user_data["admin_files_batch"])
    
    new_msg = await update.message.reply_text(
        f"📥 **تم استلام المادة رقم ({count}/{MAX_BATCH_FILES}) بنجاح!**\nارسل المادة التالية أو اضغط الزر أدناه عند الاكتفاء:",
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
        keyboard = [[InlineKeyboardButton("❌ إلغاء والعودة للإدارة", callback_data="adm_cancel_op")]]
        await query.edit_message_text("❌ لم يتم إرسال أي مواد بعد! يرجى إرسال ملف أو نص أولاً.", reply_markup=InlineKeyboardMarkup(keyboard))
        return ADMIN_WAITING_FILES

    keyboard = [[InlineKeyboardButton("❌ إلغاء والتراجع", callback_data="adm_cancel_op")]]
    await query.edit_message_text(
        f"✍️ تم جمع `{len(files)}` مواد.\nأرسل الآن **الاسم/العنوان الظاهر للمجموعة** للطلاب في الشات:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
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
    db[target_cat][item_key] = {
        "title": title,
        "files": files
    }
    save_data(db)

    context.user_data.clear()

    keyboard = [[InlineKeyboardButton("« العودة للوحة الإدارة", callback_data="adm_main")]]
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🎉 **تمت إضافة المحتوى بنجاح!**\n"
             f"━━━━━━━ • 📑 • ━━━━━━━\n\n"
             f"📌 **العنوان:** `{title}`\n"
             f"📦 **عدد العناصر:** `{len(files)}`\n"
             f"🔄 تم تحديث التغييرات ومزامنتها تلقائياً مع GitHub! 🚀",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return ConversationHandler.END

# ------------------------------------------
# 🗑️ نظام الحذف والتراجع والمسح
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
        await query.delete_message()
        return

    if data == "adm_del_select_cat":
        keyboard = [
            [InlineKeyboardButton("🎙️ قسم الإذاعات", callback_data="admdelcat_broadcasts")],
            [InlineKeyboardButton("📺 قسم المقاطع المرئية", callback_data="admdelcat_videos")],
            [InlineKeyboardButton("🎧 قسم الحوارات الإذاعية", callback_data="admdelcat_dialogues")],
            [InlineKeyboardButton("🎁 قسم الأسئلة الإذاعية", callback_data="admdelcat_quizzes")],
            [InlineKeyboardButton("« العودة للوحة الإدارة", callback_data="adm_main")]
        ]
        await query.edit_message_text("🗑️ **اختر القسم الذي تريد حذف ملف منه:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("admdelcat_"):
        cat_key = data.replace("admdelcat_", "")
        items = db.get(cat_key, {})

        if not items:
            keyboard = [[InlineKeyboardButton("« العودة للوحة الإدارة", callback_data="adm_main")]]
            await query.edit_message_text("⚠️ **لا توجد ملفات في هذا القسم لحذفها.**", reply_markup=InlineKeyboardMarkup(keyboard))
            return

        keyboard = []
        for item_id, item_info in items.items():
            keyboard.append([InlineKeyboardButton(f"🗑️ {item_info['title']}", callback_data=f"admdodell:{cat_key}:{item_id}")])
        keyboard.append([InlineKeyboardButton("« العودة للوحة الإدارة", callback_data="adm_main")])

        await query.edit_message_text("اختر الملف المراد حذفه نهائياً:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("admdodell:"):
        parts = data.split(":", 2)
        keyboard = [[InlineKeyboardButton("« العودة للوحة الإدارة", callback_data="adm_main")]]
        if len(parts) == 3:
            _, cat_key, item_id = parts
            if item_id in db.get(cat_key, {}):
                del db[cat_key][item_id]
                save_data(db)
                await query.edit_message_text("✅ **تم حذف الملف وتحديث GitHub تلقائياً!**", reply_markup=InlineKeyboardMarkup(keyboard))
                return

        await query.edit_message_text("❌ لم يتم العثور على الملف.", reply_markup=InlineKeyboardMarkup(keyboard))

async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("تم إلغاء العملية الإدارية.")
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
            CallbackQueryHandler(admin_del_handler, pattern="^(adm_main|adm_cancel_op)$")
        ],
        per_user=True
    )

    app.add_handler(admin_conv)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(CallbackQueryHandler(admin_del_handler, pattern="^adm_"))
    app.add_handler(CallbackQueryHandler(admin_del_handler, pattern="^admdelcat_"))
    app.add_handler(CallbackQueryHandler(admin_del_handler, pattern="^admdodell:"))

    print("✅ HR Bot يعمل بالهيكلة المجمعة والأنيقة بنجاح!")
    app.run_polling()

if __name__ == "__main__":
    main()

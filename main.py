import os
import json
import logging
import threading
import base64
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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

# حالات المحادثة للإدارة
ADMIN_PASSCODE, ADMIN_SELECT_CAT, ADMIN_WAITING_FILE, ADMIN_WAITING_TITLE = range(4)

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
# 🏠 الشاشة الرئيسية العامة
# ------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🎙️ قسم الإذاعات المدرسية", callback_data="cat_broadcasts")],
        [InlineKeyboardButton("📺 قسم المقاطع المرئية", callback_data="cat_videos")],
        [InlineKeyboardButton("🎧 قسم الحوارات الإذاعية", callback_data="cat_dialogues")],
        [InlineKeyboardButton("🎁 قسم الأسئلة الإذاعية", callback_data="cat_quizzes")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    welcome_text = (
        "✨ **أرشيف إذاعة ابن حميد | HR Bot**\n"
        "━━━━━━━ • 📻 • ━━━━━━━\n\n"
        "مرحباً بك في المنصة الرقمية الموحدة لإدارة وتوثيق المحتوى الإذاعي.\n\n"
        "📁 **اختر أحد الأقسام التالية للاستعراض والتصفح:**"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")

# ------------------------------------------
# 🔘 معالج تصفح المستخدمين
# ------------------------------------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    db = load_data()
    back_to_main_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main")]])

    if data.startswith("cat_"):
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

        keyboard.append([InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main")])

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
        _, cat_key, item_id = data.split(":")
        item = db.get(cat_key, {}).get(item_id)

        if item:
            await query.edit_message_text("⏳ **جاري جلب الملف من سيرفرات التليجرام...**", parse_mode="Markdown")
            chat_id = query.message.chat_id

            if item.get("type") == "video":
                await context.bot.send_video(chat_id=chat_id, video=item["file_id"], caption=f"🎥 **{item['title']}**\nتم الإرسال بنجاح 🚀", parse_mode="Markdown")
            elif item.get("type") == "photo":
                await context.bot.send_photo(chat_id=chat_id, photo=item["file_id"], caption=f"🖼️ **{item['title']}**\nتم الإرسال بنجاح 🚀", parse_mode="Markdown")
            elif item.get("type") == "audio":
                await context.bot.send_audio(chat_id=chat_id, audio=item["file_id"], caption=f"🎵 **{item['title']}**\nتم الإرسال بنجاح 🚀", parse_mode="Markdown")
            else:
                await context.bot.send_document(chat_id=chat_id, document=item["file_id"], caption=f"📄 **{item['title']}**\nتم الإرسال بنجاح 🚀", parse_mode="Markdown")

            await query.delete_message()
            nav_keyboard = [
                [InlineKeyboardButton("🔙 رجوع للقسم", callback_data=f"cat_{cat_key}"), InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main")]
            ]
            await context.bot.send_message(chat_id=chat_id, text="✨ تم إرسال الملف بنجاح.", reply_markup=InlineKeyboardMarkup(nav_keyboard))
        else:
            await query.edit_message_text("❌ لم يعد هذا الملف متوفراً.", reply_markup=back_to_main_markup)

    elif data == "main":
        await start(update, context)

# ------------------------------------------
# ⚙️ نظام التحكم الإداري الحصري /admin
# ------------------------------------------
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔒 **منطقة الإدارة والتحكم (HR Admin):**\n"
        "━━━━━━━ • 🔑 • ━━━━━━━\n\n"
        "يرجى إدخال **كلمة السر المعتمدة** للوصول للوحة التحكم:",
        parse_mode="Markdown"
    )
    return ADMIN_PASSCODE

async def admin_verify_passcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()

    if user_input == PASSCODE:
        return await show_admin_panel(update, context)
    else:
        await update.message.reply_text("❌ **كلمة السر غير صحيحة.** تم إلغاء الوصول للوحة الإدارة.")
        return ConversationHandler.END

async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("➕ إضافة ملف جديد", callback_data="adm_add_start")],
        [InlineKeyboardButton("🗑️ حذف ملف من الأقسام", callback_data="adm_del_select_cat")],
        [InlineKeyboardButton("❌ إغلاق لوحة التحكم", callback_data="adm_close")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        "⚙️ **لوحة تحكم المشرفين | HR Admin Panel**\n"
        "━━━━━━━ • 🛠️ • ━━━━━━━\n\n"
        "أهلاً بك! اختر الإجراء المطلوب أدناه:"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    
    return ConversationHandler.END

# ------------------------------------------
# ➕ إدارات إضافة وحذف الملفات داخل المحادثة الإدارية
# ------------------------------------------
async def admin_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("🎙️ قسم الإذاعات", callback_data="admcat_broadcasts")],
        [InlineKeyboardButton("📺 قسم المقاطع المرئية", callback_data="admcat_videos")],
        [InlineKeyboardButton("🎧 قسم الحوارات الإذاعية", callback_data="admcat_dialogues")],
        [InlineKeyboardButton("🎁 قسم الأسئلة الإذاعية", callback_data="admcat_quizzes")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="adm_close")]
    ]
    await query.edit_message_text(
        "📁 **اختر القسم الذي تريد إضافة الملف إليه:**",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return ADMIN_SELECT_CAT

async def admin_select_cat_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    selected_cat = query.data.replace("admcat_", "")
    context.user_data["admin_target_cat"] = selected_cat

    await query.edit_message_text(
        "📥 **قم بأرسال الملف الآن في الشات** (صوت، فيديو، مستند، صورة، أو نص):",
        parse_mode="Markdown"
    )
    return ADMIN_WAITING_FILE

async def admin_receive_file_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file_id = None
    file_type = "document"

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

    if not file_id:
        await update.message.reply_text("❌ يرجى إرسال ملف صحِّيح (مستند، صورة، فيديو، أو صوت).")
        return ADMIN_WAITING_FILE

    context.user_data["admin_file_id"] = file_id
    context.user_data["admin_file_type"] = file_type

    await update.message.reply_text(
        "📥 **تم استلام الملف بنجاح!**\n\n✍️ أرسل الآن **الاسم/العنوان الظاهر للملف** للطلاب:",
        parse_mode="Markdown"
    )
    return ADMIN_WAITING_TITLE

async def admin_save_file_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    target_cat = context.user_data.get("admin_target_cat")
    file_id = context.user_data.get("admin_file_id")
    file_type = context.user_data.get("admin_file_type")

    item_key = f"item_{int(update.message.date.timestamp())}"

    db = load_data()
    db[target_cat][item_key] = {
        "title": title,
        "file_id": file_id,
        "type": file_type
    }
    save_data(db)

    context.user_data.clear()

    await update.message.reply_text(
        f"🎉 **تمت إضافة الملف بنجاح!**\n"
        f"━━━━━━━ • 📑 • ━━━━━━━\n\n"
        f"📌 **العنوان:** `{title}`\n"
        f"🔄 تم تحديث التغييرات ومزامنتها تلقائياً مع GitHub! 🚀",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

# ------------------------------------------
# 🗑️ نظام الحذف التفاعلي للأقسام
# ------------------------------------------
async def admin_del_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    db = load_data()

    if data == "adm_close":
        await query.delete_message()
        return

    if data == "adm_del_select_cat":
        keyboard = [
            [InlineKeyboardButton("🎙️ قسم الإذاعات", callback_data="admdelcat_broadcasts")],
            [InlineKeyboardButton("📺 قسم المقاطع المرئية", callback_data="admdelcat_videos")],
            [InlineKeyboardButton("🎧 قسم الحوارات الإذاعية", callback_data="admdelcat_dialogues")],
            [InlineKeyboardButton("🎁 قسم الأسئلة الإذاعية", callback_data="admdelcat_quizzes")],
            [InlineKeyboardButton("❌ إغلاق", callback_data="adm_close")]
        ]
        await query.edit_message_text("🗑️ **اختر القسم الذي تريد حذف ملف منه:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("admdelcat_"):
        cat_key = data.replace("admdelcat_", "")
        items = db.get(cat_key, {})

        if not items:
            await query.edit_message_text("⚠️ **لا توجد ملفات في هذا القسم لحذفها.**")
            return

        keyboard = []
        for item_id, item_info in items.items():
            keyboard.append([InlineKeyboardButton(f"🗑️ {item_info['title']}", callback_data=f"admdodell:{cat_key}:{item_id}")])
        keyboard.append([InlineKeyboardButton("❌ إغلاق", callback_data="adm_close")])

        await query.edit_message_text("اختر الملف المراد حذفه نهائياً:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("admdodell:"):
        _, cat_key, item_id = data.split(":")
        if item_id in db.get(cat_key, {}):
            del db[cat_key][item_id]
            save_data(db)
            await query.edit_message_text("✅ **تم حذف الملف وتحديث GitHub تلقائياً!**")
        else:
            await query.edit_message_text("❌ لم يتم العثور على الملف.")

async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("تم إلغاء العملية الإدارية.")
    return ConversationHandler.END

# ------------------------------------------
# 🚀 تشغيل البوت
# ------------------------------------------
def main():
    threading.Thread(target=run_dummy_server, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).build()

    # محادثة الإدارة
    admin_conv = ConversationHandler(
        entry_points=[
            CommandHandler("admin", admin_command),
            CallbackQueryHandler(admin_add_start, pattern="^adm_add_start$")
        ],
        states={
            ADMIN_PASSCODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_verify_passcode)],
            ADMIN_SELECT_CAT: [CallbackQueryHandler(admin_select_cat_step, pattern="^admcat_")],
            ADMIN_WAITING_FILE: [MessageHandler((filters.Document.ALL | filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE) & ~filters.COMMAND, admin_receive_file_step)],
            ADMIN_WAITING_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_save_file_step)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv)],
        per_user=True
    )

    app.add_handler(admin_conv)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(admin_del_handler, pattern="^adm_"))
    app.add_handler(CallbackQueryHandler(admin_del_handler, pattern="^admdelcat_"))
    app.add_handler(CallbackQueryHandler(admin_del_handler, pattern="^admdodell:"))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("✅ HR Bot جاهز ومزود بنظام /admin الحصري المنسق!")
    app.run_polling()

if __name__ == "__main__":
    main()

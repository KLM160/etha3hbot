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

# 🔒 كلمة السر المعتمدة للإضافة والتعديل
PASSCODE = os.environ.get("PASSCODE", "ibnhomaid11")

DATA_FILE = "data.json"

# حالات المحادثة
WAITING_PASSCODE, WAITING_FILE, WAITING_TITLE = range(3)

# ------------------------------------------
# 🌐 السيرفر الوهمي لمنصة Render
# ------------------------------------------
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"HR Bot is Running Successfully!")

def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHandler)
    server.serve_forever()

# ------------------------------------------
# 📂 إدارة البيانات و GitHub
# ------------------------------------------
DEFAULT_DATA = {
    "broadcasts": {},
    "videos": {},
    "dialogues": {},
    "quizzes": {}
}

def load_data():
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_DATA, f, ensure_ascii=False, indent=4)
        return DEFAULT_DATA
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
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
            "message": "🔄 تحديث تلقائي لأرشيف الإذاعة",
            "content": encoded_content,
        }
        if sha:
            payload["sha"] = sha

        requests.put(url, headers=headers, json=payload)
    except Exception as e:
        print(f"GitHub Sync Error: {e}")

# ------------------------------------------
# 🎙️ الواجهة الرئيسية للبوت
# ------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # إنهاء أي محادثة أو عملية رفع سابقة عند العودة للرئيسية
    context.user_data.clear()

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
        "📁 **يرجى اختيار القسم المطلوب للاستعراض أو الإضافة:**"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")

# ------------------------------------------
# 🔘 معالج الأزرار وعرض الأقسام
# ------------------------------------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    db = load_data()

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
        
        # عرض كافة الملفات المضافة داخل القسم
        for item_id, item_info in items.items():
            keyboard.append([InlineKeyboardButton(f"📄 {item_info['title']}", callback_data=f"get_{cat_key}_{item_id}")])

        # زر الإضافة مع زر العودة للرئيسية
        keyboard.append([InlineKeyboardButton("➕ إضافة ملف لهذا القسم", callback_data=f"add_{cat_key}")])
        keyboard.append([InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main")])

        if not items:
            text = (
                f"📂 **{cat_names.get(cat_key, '')}**\n"
                f"━━━━━━━ • 📑 • ━━━━━━━\n\n"
                f"⚠️ **لا توجد ملفات مضافة في هذا القسم حالياً.**\n\n"
                f"يمكنك إضافة ملف جديد بالضغط على الزر أدناه:"
            )
        else:
            text = (
                f"📂 **{cat_names.get(cat_key, '')}**\n"
                f"━━━━━━━ • 📑 • ━━━━━━━\n\n"
                f"👇 **اختر الملف الذي تريده من القائمة أدناه ليتم إرساله لك فوراً:**"
            )

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("get_"):
        parts = data.split("_")
        cat_key, item_id = parts[1], parts[2]
        item = db.get(cat_key, {}).get(item_id)

        if item:
            await query.edit_message_text("⏳ **جاري إرسال الملف من سيرفرات التليجرام...**", parse_mode="Markdown")
            chat_id = query.message.chat_id

            # إرسال الملف باستخدام file_id من سيرفرات التليجرام مباشرة
            if item["type"] == "text":
                await context.bot.send_message(chat_id=chat_id, text=f"📌 **{item['title']}**\n\n{item['text']}", parse_mode="Markdown")
            elif item["type"] == "video":
                await context.bot.send_video(chat_id=chat_id, video=item["file_id"], caption=f"🎥 **{item['title']}**")
            elif item["type"] == "photo":
                await context.bot.send_photo(chat_id=chat_id, photo=item["file_id"], caption=f"🖼️ **{item['title']}**")
            elif item["type"] == "audio":
                await context.bot.send_audio(chat_id=chat_id, audio=item["file_id"], caption=f"🎵 **{item['title']}**")
            else:
                await context.bot.send_document(chat_id=chat_id, document=item["file_id"], caption=f"📄 **{item['title']}**")

            await query.delete_message()
            nav_keyboard = [
                [InlineKeyboardButton("🔙 رجوع للقسم", callback_data=f"cat_{cat_key}"), InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main")]
            ]
            await context.bot.send_message(
                chat_id=chat_id, 
                text="✅ **تم إرسال الملف بنجاح!**", 
                reply_markup=InlineKeyboardMarkup(nav_keyboard),
                parse_mode="Markdown"
            )

    elif data == "main":
        await start(update, context)

# ------------------------------------------
# 🔐 نظام إضافة الملفات بكلمة السر
# ------------------------------------------
async def start_add_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    cat_key = query.data.replace("add_", "")
    context.user_data["target_cat"] = cat_key

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 رجوع للقسم", callback_data=f"cat_{cat_key}"), InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main")]
    ])

    await query.edit_message_text(
        "🔒 **منطقة الإدارة والرفع:**\n"
        "━━━━━━━ • 🔑 • ━━━━━━━\n\n"
        "رجاءً أدخل **كلمة السر المعتمدة** لتتمكن من رفع الملف:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    return WAITING_PASSCODE

async def check_passcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    target_cat = context.user_data.get("target_cat", "broadcasts")

    if user_input == PASSCODE:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 إلغاء والرجوع للقسم", callback_data=f"cat_{target_cat}"), InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main")]
        ])
        await update.message.reply_text(
            "✅ **تم التحقق بنجاح!**\n\n"
            "📥 قم بأرسال **الملف** الآن (صوت، فيديو، مستند، صورة، أو نص):",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        return WAITING_FILE
    else:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 رجوع للقسم", callback_data=f"cat_{target_cat}"), InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main")]
        ])
        await update.message.reply_text("❌ **كلمة السر غير صحيحة.**", reply_markup=keyboard)
        return ConversationHandler.END

async def receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file_id = None
    file_type = "text"
    text_content = None

    # استخراج الـ File ID المباشر من سيرفرات التليجرام
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

    context.user_data["file_id"] = file_id
    context.user_data["file_type"] = file_type
    context.user_data["text_content"] = text_content

    target_cat = context.user_data.get("target_cat", "broadcasts")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 إلغاء والرجوع للقسم", callback_data=f"cat_{target_cat}"), InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main")]
    ])

    await update.message.reply_text(
        "📥 **تم استلام الملف وقراءة الـ ID التلقائي من سيرفرات التليجرام!**\n\n"
        "✍️ أرسل الآن **الاسم/العنوان** المفضّل لعرض هذا الملف في القائمة:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    return WAITING_TITLE

async def receive_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    target_cat = context.user_data.get("target_cat")

    item_key = f"item_{int(update.message.date.timestamp())}"

    db = load_data()
    db[target_cat][item_key] = {
        "title": title,
        "type": context.user_data.get("file_type"),
        "file_id": context.user_data.get("file_id"),
        "text": context.user_data.get("text_content")
    }
    save_data(db)

    context.user_data.clear()

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 رجوع للقسم", callback_data=f"cat_{target_cat}"), InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main")]
    ])
    await update.message.reply_text(
        f"🎉 **تمت إضافة الملف بنجاح!**\n"
        f"━━━━━━━ • 📑 • ━━━━━━━\n\n"
        f"📌 **عنوان الملف:** `{title}`\n"
        f"🔄 تم حفظ الـ ID ومزامنة البيانات تلقائياً مع GitHub.",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("تم إلغاء العملية.")
    return ConversationHandler.END

# ------------------------------------------
# 🚀 تشغيل البوت
# ------------------------------------------
def main():
    threading.Thread(target=run_dummy_server, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).build()

    add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_add_process, pattern="^add_")],
        states={
            WAITING_PASSCODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, check_passcode),
                CallbackQueryHandler(button_handler, pattern="^(cat_|main)")
            ],
            WAITING_FILE: [
                MessageHandler((filters.Document.ALL | filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE | filters.TEXT) & ~filters.COMMAND, receive_file),
                CallbackQueryHandler(button_handler, pattern="^(cat_|main)")
            ],
            WAITING_TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_title),
                CallbackQueryHandler(button_handler, pattern="^(cat_|main)")
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(button_handler)
        ],
        per_user=True
    )

    app.add_handler(add_conv)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("✅ HR Bot is active and running.")
    app.run_polling()

if __name__ == "__main__":
    main()

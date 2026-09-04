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

# إعداد السجل (Logging)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

TOKEN = os.environ.get("TOKEN")
GH_TOKEN = os.environ.get("GH_TOKEN")
GH_REPO = os.environ.get("GH_REPO")

if not TOKEN:
    raise ValueError("❌ خطأ: لم يتم العثور على التوكن!")

# 🔒 كلمة السر المعتمدة للإضافة والتعديل
PASSCODE = os.environ.get("PASSCODE", "ibnhomaid11")

DATA_FILE = "data.json"

# حالات المحادثة
WAITING_PASSCODE, WAITING_CAT, WAITING_TITLE = range(3)

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
# 🌐 السيرفر الوهمي لمنصة Render
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
# 📥 استقبال الملف واستخراج الـ ID تلقائياً
# ------------------------------------------
async def handle_incoming_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file_id = None
    file_type = "document"
    file_name = "ملف جديد"

    if update.message.document:
        file_id = update.message.document.file_id
        file_name = update.message.document.file_name or "مستند"
        file_type = "document"
    elif update.message.video:
        file_id = update.message.video.file_id
        file_name = "مقطع مرئي"
        file_type = "video"
    elif update.message.photo:
        file_id = update.message.photo[-1].file_id
        file_name = "صورة"
        file_type = "photo"
    elif update.message.audio or update.message.voice:
        file_id = update.message.audio.file_id if update.message.audio else update.message.voice.file_id
        file_name = "مقطع صوتي"
        file_type = "audio"

    if file_id:
        context.user_data["pending_file_id"] = file_id
        context.user_data["pending_file_type"] = file_type

        keyboard = [
            [InlineKeyboardButton("➕ إضافة هذا الملف للأرشيف", callback_data="start_add_flow")],
            [InlineKeyboardButton("❌ إغلاق", callback_data="close_msg")]
        ]

        await update.message.reply_text(
            f"✅ **تم استلام الملف بنجاح!** ({file_name})\n\n"
            f"🔑 **File ID المباشر:**\n`{file_id}`\n\n"
            f"اضغط الزر أدناه لإدخال كلمة السر واختيار القسم واقتراح العنوان:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

# ------------------------------------------
# 🏠 الشاشة الرئيسية للبوت
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
# 🔘 معالج التفاعلات واستعراض الأقسام
# ------------------------------------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    db = load_data()
    back_to_main_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main")]])

    if data == "close_msg":
        await query.delete_message()
        return

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
            keyboard.append([InlineKeyboardButton(f"📄 {item_info['title']}", callback_data=f"dl_{cat_key}_{item_id}")])

        keyboard.append([InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main")])

        if not items:
            text = (
                f"📂 **{cat_names.get(cat_key, '')}**\n"
                f"━━━━━━━ • 📑 • ━━━━━━━\n\n"
                f"⚠️ **لا توجد ملفات مضافة في هذا القسم حالياً.**\n\n"
                f"لإضافة ملف جديد، أرسل الملف مباشرة للشات ثم اضغط على زر الإضافة."
            )
        else:
            text = (
                f"📂 **{cat_names.get(cat_key, '')}**\n"
                f"━━━━━━━ • 📑 • ━━━━━━━\n\n"
                f"👇 **اختر الملف الذي تريده من القائمة أدناه ليتم إرساله لك فوراً:**"
            )

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("dl_"):
        parts = data.split("_")
        cat_key, item_id = parts[1], parts[2]
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
# 🔐 محادثة التحقق بكلمة السر وتحديد القسم والعنوان
# ------------------------------------------
async def start_add_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not context.user_data.get("pending_file_id"):
        await query.edit_message_text("❌ لم نجد ملفاً معلقاً. يرجى إعادة إرسال الملف للشات أولاً.")
        return ConversationHandler.END

    await query.edit_message_text(
        "🔒 **منطقة محمية (رفع المحتوى):**\n\n"
        "🔑 أدخل **كلمة السر المعتمدة** للمتابعة:",
        parse_mode="Markdown"
    )
    return WAITING_PASSCODE

async def check_passcode_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()

    if user_input == PASSCODE:
        keyboard = [
            [InlineKeyboardButton("🎙️ إذاعات مدرسية", callback_data="addcat_broadcasts")],
            [InlineKeyboardButton("📺 مقاطع مرئية", callback_data="addcat_videos")],
            [InlineKeyboardButton("🎧 حوارات إذاعية", callback_data="addcat_dialogues")],
            [InlineKeyboardButton("🎁 أسئلة إذاعية", callback_data="addcat_quizzes")]
        ]
        await update.message.reply_text(
            "✅ **رمز صحيح!**\n\n📁 **اختر القسم الذي تريد إضافة الملف إليه:**",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return WAITING_CAT
    else:
        await update.message.reply_text("❌ كلمة السر غير صحيحة. تم إلغاء العملية.")
        return ConversationHandler.END

async def select_category_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    selected_cat = query.data.replace("addcat_", "")
    context.user_data["selected_cat"] = selected_cat

    await query.edit_message_text(
        "✍️ أرسل الآن **الاسم/العنوان الظاهر للملف** للطلاب في الشات (مثال: `إذاعة الأسبوع الأول`):",
        parse_mode="Markdown"
    )
    return WAITING_TITLE

async def save_file_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    file_id = context.user_data.get("pending_file_id")
    file_type = context.user_data.get("pending_file_type", "document")
    target_cat = context.user_data.get("selected_cat", "broadcasts")

    if not file_id:
        await update.message.reply_text("❌ حدث خطأ، لم يتم العثور على كود الملف.")
        return ConversationHandler.END

    item_key = f"item_{int(update.message.date.timestamp())}"

    db = load_data()
    db[target_cat][item_key] = {
        "title": title,
        "file_id": file_id,
        "type": file_type
    }
    save_data(db)

    # تفريغ البيانات المعلقة
    context.user_data.clear()

    keyboard = [[InlineKeyboardButton("🔙 الذهاب للقسم", callback_data=f"cat_{target_cat}"), InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main")]]
    await update.message.reply_text(
        f"🎉 **تمت إضافة الملف بنجاح!**\n\n"
        f"📌 **العنوان:** `{title}`\n"
        f"🔄 تم تحديث ملف `data.json` ومزامنته مع GitHub تلقائياً! 🚀",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("تم إلغاء العملية.")
    return ConversationHandler.END

# ------------------------------------------
# 🚀 تشغيل البوت
# ------------------------------------------
def main():
    threading.Thread(target=run_dummy_server, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).build()

    add_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_add_flow, pattern="^start_add_flow$")
        ],
        states={
            WAITING_PASSCODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_passcode_step)],
            WAITING_CAT: [CallbackQueryHandler(select_category_step, pattern="^addcat_")],
            WAITING_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_file_step)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv)],
        per_user=True
    )

    app.add_handler(add_conv)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))

    # معالج استقبال وسائط الإعلام والملفات الشبيه بكود أرشيف الثانوية
    media_filter = filters.Document.ALL | filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE
    app.add_handler(MessageHandler(media_filter, handle_incoming_file))

    print("✅ HR Bot جاهز للعمل بنفس نظام التقاط الملفات والرفع بكلمة السر!")
    app.run_polling()

if __name__ == "__main__":
    main()

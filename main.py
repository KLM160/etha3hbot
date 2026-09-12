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

# مكتبة توليد الـ PDF
from fpdf import FPDF

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
INTRO_MAIN, INTRO_PARAS, INTRO_OUTRO = range(4, 7)
PARA_TITLE, PARA_CONTENT = range(7, 9)

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

DEFAULT_DATA = {"broadcasts": {}, "videos": {}, "dialogues": {}, "quizzes": {}, "general_archive": {}}

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

class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"HR Radio Archive Bot Running")

def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), SimpleHandler).serve_forever()

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
# 📄 دالة إنشاء ملفات PDF منسقة بالكامل
# ------------------------------------------
class ArabicPDF(FPDF):
    def header(self):
        self.set_font("Arial", "B", 10)
        self.cell(0, 10, "إذاعة ابن حميد المدرسية | النشاط الطلابي", border=0, ln=1, align="C")
        self.line(10, 18, 200, 18)
        self.ln(5)

def generate_pdf_document(doc_title, main_text, items_list, outro_text=None, doc_type="intro"):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    
    # تحسب الكلمات للتنسيق
    total_words = len(main_text.split()) if main_text else 0
    if outro_text:
        total_words += len(outro_text.split())
    for item in items_list:
        if isinstance(item, dict):
            total_words += len(item.get("title", "").split()) + len(item.get("content", "").split())
        else:
            total_words += len(str(item).split())

    body_size = 14 if total_words < 150 else 12

    # العنوان الرئيسي
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, doc_title, ln=True, align="C")
    pdf.ln(5)

    # المقدمة العامة
    if main_text:
        pdf.set_font("Arial", "B", body_size)
        pdf.multi_cell(0, 8, f"المقدمة العامة:\n{main_text}", align="R")
        pdf.ln(5)

    # الفقرات
    for idx, item in enumerate(items_list, start=1):
        pdf.set_font("Arial", "B", body_size + 1)
        if doc_type == "intro":
            pdf.cell(0, 8, f"مقدمة الفقرة رقم ({idx}):", ln=True, align="R")
            pdf.set_font("Arial", "", body_size)
            pdf.multi_cell(0, 8, f"{item}", align="R")
        else:
            pdf.cell(0, 8, f"فقرة: {item['title']}", ln=True, align="R")
            pdf.set_font("Arial", "", body_size)
            pdf.multi_cell(0, 8, f"{item['content']}", align="R")
        pdf.ln(4)

    # الخاتمة
    if outro_text:
        pdf.ln(3)
        pdf.set_font("Arial", "B", body_size)
        pdf.multi_cell(0, 8, f"الخاتمة:\n{outro_text}", align="R")

    filename = f"Radio_{doc_type}_{int(asyncio.get_event_loop().time())}.pdf"
    pdf.output(filename)
    return filename

# ------------------------------------------
# 🏠 الشاشة الرئيسية
# ------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    keyboard = [
        [InlineKeyboardButton("📁 الأرشيف الإذاعي", callback_data="archive_menu")],
        [InlineKeyboardButton("🎙️ تنسيق إذاعة (PDF)", callback_data="formatting_menu")],
        [InlineKeyboardButton("🗄️ الأرشيف العام (المحتوى المكتمل)", callback_data="cat_general_archive")],
        [InlineKeyboardButton("ℹ️ عن الأرشيف", callback_data="about_info")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = (
        "✨ **أرشيف إذاعة ابن حميد | HR Bot**\n"
        "━━━━━━━ • 📻 • ━━━━━━━\n\n"
        "مرحباً بك في المنصة الرقمية الموحدة لإدارة وتوثيق المحتوى الإذاعي.\n\n"
        "اختر أحد الخيارات التالية للاستعراض أو تنسيق ملفاتك:"
    )
    if update.callback_query:
        await safe_edit_text(update.callback_query, text, reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")

# ------------------------------------------
# 📝 محادثة تنسيق المقدمات والفقرات
# ------------------------------------------
async def start_intro_formatter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["intro_paras"] = []
    
    keyboard = [[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_conv")]]
    await safe_edit_text(query, "📝 **تنسيق المقدمة الإذاعية:**\n\nأرسل **المقدمة العامة** للإذاعة الآن:", InlineKeyboardMarkup(keyboard))
    return INTRO_MAIN

async def receive_intro_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["intro_main_text"] = update.message.text.strip()
    
    keyboard = [[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_conv")]]
    await update.message.reply_text(
        f"✅ تم حفظ المقدمة العامة.\n\nأرسل الآن **مقدمة الفقرة الأولى**:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return INTRO_PARAS

async def receive_intro_paras(update: Update, context: ContextTypes.DEFAULT_TYPE):
    paras = context.user_data.get("intro_paras", [])
    paras.append(update.message.text.strip())
    context.user_data["intro_paras"] = paras

    keyboard = [
        [InlineKeyboardButton("✅ اكتفيت بالفقرات (انتقل للخاتمة)", callback_data="intro_done_paras")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_conv")]
    ]
    await update.message.reply_text(
        f"✅ تم حفظ مقدمة الفقرة رقم ({len(paras)}).\n\nأرسل **مقدمة الفقرة رقم ({len(paras)+1})** أو اضغط الزر للتجاوز:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return INTRO_PARAS

async def intro_done_paras_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    keyboard = [[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_conv")]]
    await safe_edit_text(query, "✍️ أرسل الآن **الخاتمة الإذاعية**:", InlineKeyboardMarkup(keyboard))
    return INTRO_OUTRO

async def receive_intro_outro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    outro_text = update.message.text.strip()
    main_text = context.user_data.get("intro_main_text")
    paras = context.user_data.get("intro_paras", [])

    await update.message.reply_text("⏳ **جاري تنسيق وإنشاء ملف PDF المنسق...**")

    filename = generate_pdf_document("إذاعة مدرسية - مقدمات منسقة", main_text, paras, outro_text, doc_type="intro")

    await update.message.reply_document(
        document=open(filename, "rb"),
        filename="مقدمة_إذاعية_منسقة.pdf",
        caption="🎉 **تم تنسيق المقدمة وتجهيز ملف PDF بنجاح!**"
    )
    if os.path.exists(filename):
        os.remove(filename)

    context.user_data.clear()
    return ConversationHandler.END

async def start_para_formatter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["para_items"] = []

    keyboard = [[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_conv")]]
    await safe_edit_text(query, "📖 **تنسيق الفقرات الإذاعية:**\n\nأرسل **عنوان الفقرة الأولى** (مثال: القرآن الكريم، هل تعلم):", InlineKeyboardMarkup(keyboard))
    return PARA_TITLE

async def receive_para_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["temp_para_title"] = update.message.text.strip()

    keyboard = [[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_conv")]]
    await update.message.reply_text(
        f"📌 **العنوان:** {context.user_data['temp_para_title']}\n\nأرسل الآن **محتوى هذه الفقرة**:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return PARA_CONTENT

async def receive_para_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = context.user_data.get("para_items", [])
    title = context.user_data.get("temp_para_title")
    content = update.message.text.strip()

    items.append({"title": title, "content": content})
    context.user_data["para_items"] = items

    keyboard = [
        [InlineKeyboardButton("➕ إضافة فقرة أخرى", callback_data="para_add_more")],
        [InlineKeyboardButton("✅ اكتفيت بالفقرات وأنشئ الملف", callback_data="para_done_all")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_conv")]
    ]
    await update.message.reply_text(
        f"✅ تم حفظ فقرة ({title}) بنجاح!\nعدد الفقرات الحالية: `{len(items)}`\n\nاختر خطوتك التالية:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return PARA_TITLE

async def para_add_more_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton("❌ إلغاء", callback_data="cancel_conv")]]
    await safe_edit_text(query, "أرسل **عنوان الفقرة التالية**:", InlineKeyboardMarkup(keyboard))
    return PARA_TITLE

async def para_done_all_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    items = context.user_data.get("para_items", [])

    if not items:
        await safe_edit_text(query, "❌ لم تقم بإضافة أي فقرة بعد!")
        return ConversationHandler.END

    await safe_edit_text(query, "⏳ **جاري تنسيق وإنشاء ملف PDF المنسق...**")

    filename = generate_pdf_document("فقرات إذاعية منسقة", None, items, None, doc_type="paragraphs")

    chat_id = query.message.chat_id
    await context.bot.send_document(
        chat_id=chat_id,
        document=open(filename, "rb"),
        filename="فقرات_إذاعية_منسقة.pdf",
        caption="🎉 **تم تنسيق الفقرات وتجهيز ملف PDF بنجاح!**"
    )
    if os.path.exists(filename):
        os.remove(filename)

    context.user_data.clear()
    return ConversationHandler.END

# ------------------------------------------
# 🔘 معالج الأزرار العامة والأرشيف
# ------------------------------------------
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

    elif data == "formatting_menu":
        keyboard = [
            [InlineKeyboardButton("📝 تنسيق مقدمة إذاعية (PDF)", callback_data="start_intro_formatter")],
            [InlineKeyboardButton("📖 تنسيق فقرات إذاعية (PDF)", callback_data="start_para_formatter")],
            [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main")]
        ]
        text = "🎙️ **قسم تنسيق الإذاعة**\n━━━━━━━ • 📑 • ━━━━━━━\n\nاختر نوع التنسيق المطلوب لبدء إنشاء ملف PDF المنسق:"
        await safe_edit_text(query, text, InlineKeyboardMarkup(keyboard))

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
            "quizzes": "🎁 قسم الأسئلة الإذاعية",
            "general_archive": "🗄️ الأرشيف العام (المحتوى المكتمل)"
        }

        keyboard = []
        text = f"📂 **{cat_names.get(cat_key, '')}**\n━━━━━━━ • 📑 • ━━━━━━━\n\n"

        if not items:
            text += "⚠️ **لا توجد ملفات مضافة حالياً.**"
        else:
            text += "👇 **قائمة المحتوى المتاح:**\n\n"
            total_items = len(items)
            for idx, (item_id, info) in enumerate(items.items(), start=1):
                text += f"📌 **{idx}. {info['title']}**\n"
                keyboard.append([InlineKeyboardButton(f"📥 استعراض المحتوى رقم ({idx})", callback_data=f"dl:{cat_key}:{item_id}")])
                
                if idx < total_items:
                    text += "━━━━━━━ • 📌 • ━━━━━━━\n"

        back_btn = [InlineKeyboardButton("🔙 رجوع للأرشيف", callback_data="archive_menu")] if cat_key != "general_archive" else []
        keyboard.append(back_btn + [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main")])
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

                await context.bot.send_message(
                    chat_id=chat_id, 
                    text=f"━━━━━━━ • 📻 **طلب جديد** • ━━━━━━━\n📦 **المحتوى:** {item['title']}", 
                    parse_mode="Markdown"
                )

                for idx, f in enumerate(file_list):
                    f_id, f_type, f_text = f.get("file_id"), f.get("type"), f.get("text")
                    if f_type == "text":
                        await context.bot.send_message(chat_id=chat_id, text=f_text, parse_mode="Markdown")
                    elif f_type == "video":
                        await context.bot.send_video(chat_id=chat_id, video=f_id)
                    elif f_type == "photo":
                        await context.bot.send_photo(chat_id=chat_id, photo=f_id)
                    elif f_type == "audio":
                        await context.bot.send_audio(chat_id=chat_id, audio=f_id)
                    else:
                        await context.bot.send_document(chat_id=chat_id, document=f_id)

                    if idx < len(file_list) - 1:
                        await asyncio.sleep(0.3)

                await safe_delete_msg(context, chat_id, query.message.message_id)
                back_target = "cat_general_archive" if cat_key == "general_archive" else f"cat_{cat_key}"
                nav_kb = [[InlineKeyboardButton("🔙 رجوع للقسم", callback_data=back_target), InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main")]]

                final_caption = f"✨ **تم إرسال المحتوى الموصل بنجاح:**\n📌 **{item['title']}**\n━━━━━━━ • 🏁 • ━━━━━━━"
                await context.bot.send_message(chat_id=chat_id, text=final_caption, reply_markup=InlineKeyboardMarkup(nav_kb), parse_mode="Markdown")
            else:
                await safe_edit_text(query, "❌ لم يعد هذا المحتوى متوفراً.", back_main)

# ------------------------------------------
# ⚙️ نظام الإدارة /admin
# ------------------------------------------
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    keyboard = [[InlineKeyboardButton("❌ إلغاء الإدخال", callback_data="adm_cancel_passcode")]]
    msg = await update.message.reply_text(
        "🔒 **منطقة الإدارة والتحكم:**\nيرجى إدخال **كلمة السر المعتمدة**:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    context.user_data["last_msg_id"] = msg.message_id
    return ADMIN_PASSCODE

async def admin_cancel_passcode_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await safe_delete_msg(context, query.message.chat_id, query.message.message_id)
    await context.bot.send_message(chat_id=query.message.chat_id, text="🚫 تم إلغاء دخول الإدارة.")
    return ConversationHandler.END

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
        [InlineKeyboardButton("📦 نقل محتوى إلى الأرشيف العام", callback_data="adm_move_select_cat")],
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

async def admin_del_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()
    db = load_data()

    if data in ["adm_main", "adm_cancel_op"]:
        context.user_data.clear()
        await show_admin_panel(update, context)
        return ConversationHandler.END

    if data == "adm_close":
        context.user_data.clear()
        await safe_delete_msg(context, query.message.chat_id, query.message.message_id)
        return ConversationHandler.END

    if data == "adm_del_select_cat":
        keyboard = [
            [InlineKeyboardButton("🎙️ قسم الإذاعات", callback_data="admdelcat_broadcasts")],
            [InlineKeyboardButton("📺 قسم المقاطع المرئية", callback_data="admdelcat_videos")],
            [InlineKeyboardButton("🎧 قسم الحوارات الإذاعية", callback_data="admdelcat_dialogues")],
            [InlineKeyboardButton("🎁 قسم الأسئلة الإذاعية", callback_data="admdelcat_quizzes")],
            [InlineKeyboardButton("🗄️ الأرشيف العام", callback_data="admdelcat_general_archive")],
            [InlineKeyboardButton("« العودة للوحة الإدارة", callback_data="adm_main")]
        ]
        await safe_edit_text(query, "🗑️ **اختر القسم الذي تريد حذف ملف منه:**", InlineKeyboardMarkup(keyboard))

    elif data.startswith("admdelcat_"):
        cat_key = data.replace("admdelcat_", "")
        items = db.get(cat_key, {})
        if not items:
            await safe_edit_text(query, "⚠️ **لا توجد ملفات في هذا القسم لحذفها.**", InlineKeyboardMarkup([[InlineKeyboardButton("« العودة للوحة الإدارة", callback_data="adm_main")]]))
            return

        keyboard = []
        text = "🗑️ **اختر المحتوى المراد حذفه نهائياً:**\n\n"
        for idx, (item_id, info) in enumerate(items.items(), start=1):
            text += f"📌 **{idx}. {info['title']}**\n"
            keyboard.append([InlineKeyboardButton(f"🗑️ حذف رقم ({idx})", callback_data=f"admdodell:{cat_key}:{item_id}")])

        keyboard.append([InlineKeyboardButton("« العودة للوحة الإدارة", callback_data="adm_main")])
        await safe_edit_text(query, text, InlineKeyboardMarkup(keyboard))

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

    elif data == "adm_move_select_cat":
        keyboard = [
            [InlineKeyboardButton("🎙️ قسم الإذاعات", callback_data="admmovecat_broadcasts")],
            [InlineKeyboardButton("📺 قسم المقاطع المرئية", callback_data="admmovecat_videos")],
            [InlineKeyboardButton("🎧 قسم الحوارات الإذاعية", callback_data="admmovecat_dialogues")],
            [InlineKeyboardButton("🎁 قسم الأسئلة الإذاعية", callback_data="admmovecat_quizzes")],
            [InlineKeyboardButton("« العودة للوحة الإدارة", callback_data="adm_main")]
        ]
        await safe_edit_text(query, "📦 **اختر القسم الرئيسي لنقل محتوى منه إلى (الأرشيف العام):**", InlineKeyboardMarkup(keyboard))

    elif data.startswith("admmovecat_"):
        cat_key = data.replace("admmovecat_", "")
        items = db.get(cat_key, {})
        if not items:
            await safe_edit_text(query, "⚠️ **لا توجد ملفات في هذا القسم لنقلها.**", InlineKeyboardMarkup([[InlineKeyboardButton("« العودة للوحة الإدارة", callback_data="adm_main")]]))
            return

        keyboard = []
        text = "📦 **اختر المحتوى المراد نقل الأرشيف العام:**\n\n"
        for idx, (item_id, info) in enumerate(items.items(), start=1):
            text += f"📌 **{idx}. {info['title']}**\n"
            keyboard.append([InlineKeyboardButton(f"📦 نقل رقم ({idx})", callback_data=f"admdomove:{cat_key}:{item_id}")])

        keyboard.append([InlineKeyboardButton("« العودة للوحة الإدارة", callback_data="adm_main")])
        await safe_edit_text(query, text, InlineKeyboardMarkup(keyboard))

    elif data.startswith("admdomove:"):
        parts = data.split(":", 2)
        if len(parts) == 3:
            _, cat_key, item_id = parts
            item = db.get(cat_key, {}).get(item_id)
            if item:
                if "general_archive" not in db:
                    db["general_archive"] = {}
                db["general_archive"][item_id] = item
                del db[cat_key][item_id]
                
                save_data_instant(db)
                asyncio.create_task(delayed_github_sync())
                
                await safe_edit_text(
                    query, 
                    f"✅ **تم نقل المحتوى بنجاح إلى (الأرشيف العام)!**\n📌 **العنوان:** `{item['title']}`", 
                    InlineKeyboardMarkup([[InlineKeyboardButton("« العودة للوحة الإدارة", callback_data="adm_main")]])
                )
                return
        await safe_edit_text(query, "❌ لم يتم العثور على الملف لنقله.", InlineKeyboardMarkup([[InlineKeyboardButton("« العودة للوحة الإدارة", callback_data="adm_main")]]))

async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        await safe_edit_text(query, "🚫 **تم إلغاء العملية.**")
    else:
        await update.message.reply_text("🚫 **تم إلغاء العملية.**")
    context.user_data.clear()
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
            ADMIN_PASSCODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_verify_passcode),
                CallbackQueryHandler(admin_cancel_passcode_handler, pattern="^adm_cancel_passcode$")
            ],
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
            CallbackQueryHandler(cancel_conv, pattern="^cancel_conv$"),
            CallbackQueryHandler(admin_del_handler, pattern="^(adm_main|adm_cancel_op)$")
        ],
        per_user=True
    )

    # محادثة تنسيق المقدمة
    intro_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_intro_formatter, pattern="^start_intro_formatter$")],
        states={
            INTRO_MAIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_intro_main)],
            INTRO_PARAS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_intro_paras),
                CallbackQueryHandler(intro_done_paras_handler, pattern="^intro_done_paras$")
            ],
            INTRO_OUTRO: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_intro_outro)],
        },
        fallbacks=[CallbackQueryHandler(cancel_conv, pattern="^cancel_conv$")],
        per_user=True
    )

    # محادثة تنسيق الفقرات
    para_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_para_formatter, pattern="^start_para_formatter$")],
        states={
            PARA_TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_para_title),
                CallbackQueryHandler(para_add_more_handler, pattern="^para_add_more$")
            ],
            PARA_CONTENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_para_content),
                CallbackQueryHandler(para_done_all_handler, pattern="^para_done_all$")
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_conv, pattern="^cancel_conv$"),
            CallbackQueryHandler(para_done_all_handler, pattern="^para_done_all$")
        ],
        per_user=True
    )

    app.add_handler(admin_conv)
    app.add_handler(intro_conv)
    app.add_handler(para_conv)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(admin_del_handler, pattern="^adm_"))
    app.add_handler(CallbackQueryHandler(admin_del_handler, pattern="^admdelcat_"))
    app.add_handler(CallbackQueryHandler(admin_del_handler, pattern="^admdodell:"))
    app.add_handler(CallbackQueryHandler(admin_del_handler, pattern="^admmovecat_"))
    app.add_handler(CallbackQueryHandler(admin_del_handler, pattern="^admdomove:"))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("✅ HR Bot يعمل بأحدث تنسيق ومحمي بالكامل!")
    app.run_polling()

if __name__ == "__main__":
    main()

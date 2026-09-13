import asyncio, base64, json, logging, os, threading, requests
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

TOKEN, GH_TOKEN, GH_REPO = os.environ.get("TOKEN"), os.environ.get("GH_TOKEN"), os.environ.get("GH_REPO")
if not TOKEN: raise ValueError("❌ خطأ: لم يتم العثور على التوكن!")

PASSCODE, DATA_FILE, MAX_BATCH_FILES = os.environ.get("PASSCODE", "ibnhomaid11"), "data.json", 10
ADMIN_PASSCODE, ADMIN_SELECT_CAT, ADMIN_WAITING_FILES, ADMIN_WAITING_TITLE = range(4)

ABOUT_TEXT = "🎙️ **عن أرشيف إذاعة ابن حميد | HR Bot**\n━━━━━━━ • 📻 • ━━━━━━━\n\nمنصة رقمية لتوثيق وأرشفة المحتوى الإذاعي وتسهيل الوصول للأنشطة المدرسية.\n\n👨‍🏫 **الإشراف العام:** رائد النشاط أ.فهد\n💻 **التطوير والبرمجة:** AboZaid"
DEFAULT_DATA = {"broadcasts": {}, "videos": {}, "dialogues": {}, "quizzes": {}, "general_archive": {}}
CAT_NAMES = {"broadcasts": "🎙️ قسم الإذاعات", "videos": "📺 قسم المقاطع المرئية", "dialogues": "🎧 قسم الحوارات الإذاعية", "quizzes": "🎁 قسم الأسئلة الإذاعية", "general_archive": "🗄️ الأرشيف العام"}

# --- التعامل مع البيانات وسيرفر البوت ---
def load_data():
    if not os.path.exists(DATA_FILE): save_data_instant(DEFAULT_DATA); return DEFAULT_DATA
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {**DEFAULT_DATA, **data}
    except Exception as e: logging.error(f"JSON Error: {e}"); return DEFAULT_DATA

def save_data_instant(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=4)

def sync_to_github():
    if not (GH_TOKEN and GH_REPO): return
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{DATA_FILE}"
    headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        content = base64.b64encode(open(DATA_FILE, "r", encoding="utf-8").read().encode()).decode()
        get_res = requests.get(url, headers=headers)
        sha = get_res.json().get("sha") if get_res.status_code == 200 else None
        payload = {"message": "🔄 التحديث التلقائي للأرشيف", "content": content}
        if sha: payload["sha"] = sha
        requests.put(url, headers=headers, json=payload)
    except Exception as e: logging.error(f"GitHub Error: {e}")

class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers(); self.wfile.write(b"HR Radio Archive Bot Running")

def run_dummy_server(): HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 10000))), SimpleHandler).serve_forever()

async def safe_delete_msg(context, chat_id, msg_id):
    try: await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except Exception: pass

async def safe_edit_text(query, text, reply_markup=None):
    try: await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    except BadRequest: pass

# --- الشاشة الرئيسية ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📁 الأرشيف الإذاعي", callback_data="archive_menu")],
        [InlineKeyboardButton("🗄️ الأرشيف العام (المحتوى المكتمل)", callback_data="cat_general_archive")],
        [InlineKeyboardButton("ℹ️ عن الأرشيف", callback_data="about_info")]
    ])
    text = "✨ **أرشيف إذاعة ابن حميد | HR Bot**\n━━━━━━━ • 📻 • ━━━━━━━\n\nمرحباً بك في المنصة الرقمية الموحدة لتوثيق المحتوى الإذاعي.\nاختر من القائمة أدناه:"
    if update.callback_query: await safe_edit_text(update.callback_query, text, kb)
    else: await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")

# --- معالج واجهة الطلاب والأرشيف ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query, data, db = update.callback_query, update.callback_query.data, load_data()
    await query.answer()
    back_main = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main")]])

    if data == "main": await start(update, context)
    elif data == "about_info": await safe_edit_text(query, ABOUT_TEXT, back_main)
    elif data == "archive_menu":
        kb = [[InlineKeyboardButton(name, callback_data=f"cat_{k}")] for k, name in CAT_NAMES.items() if k != "general_archive"]
        kb.append([InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main")])
        await safe_edit_text(query, "📁 **الأرشيف الإذاعي**\n━━━━━━━ • 📑 • ━━━━━━━\n\nاختر القسم المطلوب:", InlineKeyboardMarkup(kb))
    elif data.startswith("cat_"):
        cat_key, items = data.replace("cat_", ""), db.get(data.replace("cat_", ""), {})
        text = f"📂 **{CAT_NAMES.get(cat_key, '')}**\n━━━━━━━ • 📑 • ━━━━━━━\n\n"
        kb = []
        if not items: text += "⚠️ **لا توجد ملفات مضافة حالياً.**"
        else:
            text += "👇 **قائمة المحتوى المتاح:**\n\n"
            for idx, (item_id, info) in enumerate(items.items(), start=1):
                text += f"📌 **{idx}. {info['title']}**\n"
                kb.append([InlineKeyboardButton(f"📥 استعراض المحتوى رقم ({idx})", callback_data=f"dl:{cat_key}:{item_id}")])
        back_btn = [InlineKeyboardButton("🔙 رجوع للأرشيف", callback_data="archive_menu")] if cat_key != "general_archive" else []
        kb.append(back_btn + [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main")])
        await safe_edit_text(query, text, InlineKeyboardMarkup(kb))
    elif data.startswith("dl:"):
        _, cat_key, item_id = data.split(":", 2)
        item = db.get(cat_key, {}).get(item_id)
        if item:
            await safe_edit_text(query, "⏳ **جاري جلب المحتوى...**")
            chat_id = query.message.chat_id
            await context.bot.send_message(chat_id=chat_id, text=f"📦 **المحتوى المطلوب:** {item['title']}")
            for f in item.get("files", []):
                t, fid, txt = f.get("type"), f.get("file_id"), f.get("text")
                if t == "text": await context.bot.send_message(chat_id, txt, parse_mode="Markdown")
                elif t == "video": await context.bot.send_video(chat_id, fid)
                elif t == "photo": await context.bot.send_photo(chat_id, fid)
                elif t == "audio": await context.bot.send_audio(chat_id, fid)
                else: await context.bot.send_document(chat_id, fid)
                await asyncio.sleep(0.3)
            await safe_delete_msg(context, chat_id, query.message.message_id)
            nav = [[InlineKeyboardButton("🔙 رجوع للقسم", callback_data=f"cat_{cat_key}"), InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main")]]
            await context.bot.send_message(chat_id=chat_id, text=f"✨ **تم إرسال المحتوى:**\n📌 **{item['title']}**", reply_markup=InlineKeyboardMarkup(nav), parse_mode="Markdown")

# --- لوحة التحكم والإدارة (/admin) ---
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    msg = await update.message.reply_text("🔒 **الإدارة:** أدخل **كلمة السر**:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="adm_cancel_op")]]), parse_mode="Markdown")
    context.user_data["last_msg_id"] = msg.message_id
    return ADMIN_PASSCODE

async def admin_verify_passcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await safe_delete_msg(context, chat_id, update.message.message_id)
    if "last_msg_id" in context.user_data: await safe_delete_msg(context, chat_id, context.user_data["last_msg_id"])
    if update.message.text.strip() == PASSCODE: return await show_admin_panel(update, context)
    context.user_data.clear(); await context.bot.send_message(chat_id, "❌ كلمة السر غير صحيحة."); return ConversationHandler.END

async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ إضافة ملفات جديدة", callback_data="adm_add_start")],
        [InlineKeyboardButton("📦 نقل للأرشيف العام", callback_data="adm_move_select_cat")],
        [InlineKeyboardButton("🗑️ حذف ملف من الأقسام", callback_data="adm_del_select_cat")],
        [InlineKeyboardButton("❌ إغلاق لوحة التحكم", callback_data="adm_close")]
    ])
    text = "⚙️ **لوحة تحكم المشرفين | HR Admin**"
    if update.callback_query: await safe_edit_text(update.callback_query, text, kb)
    else:
        msg = await context.bot.send_message(update.effective_chat.id, text, reply_markup=kb, parse_mode="Markdown")
        context.user_data["last_msg_id"] = msg.message_id
    return ConversationHandler.END

async def admin_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    kb = [[InlineKeyboardButton(name, callback_data=f"admcat_{k}")] for k, name in CAT_NAMES.items() if k != "general_archive"]
    kb.append([InlineKeyboardButton("« العودة للوحة الإدارة", callback_data="adm_main")])
    await safe_edit_text(query, "📁 **اختر القسم للإضافة:**", InlineKeyboardMarkup(kb))
    return ADMIN_SELECT_CAT

async def admin_select_cat_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    context.user_data["admin_target_cat"], context.user_data["admin_files_batch"] = query.data.replace("admcat_", ""), []
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ اكتفيت، انتقل للعنوان", callback_data="adm_done_files")], [InlineKeyboardButton("❌ إلغاء", callback_data="adm_cancel_op")]])
    await safe_edit_text(query, f"📥 **أرسل الملفات أو النصوص الآن** (حد أقصى {MAX_BATCH_FILES}):", kb)
    return ADMIN_WAITING_FILES

async def admin_receive_files_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id, batch = update.effective_chat.id, context.user_data.get("admin_files_batch", [])
    if len(batch) >= MAX_BATCH_FILES: await update.message.reply_text("⚠️ وصلت للحد الأقصى!"); return ADMIN_WAITING_FILES
    
    msg = update.message
    fid, ftype, txt = None, "document", None
    if msg.document: fid, ftype = msg.document.file_id, "document"
    elif msg.video: fid, ftype = msg.video.file_id, "video"
    elif msg.photo: fid, ftype = msg.photo[-1].file_id, "photo"
    elif msg.audio or msg.voice: fid, ftype = (msg.audio or msg.voice).file_id, "audio"
    elif msg.text: txt, ftype = msg.text, "text"

    if "prompt_msg_id" in context.user_data: await safe_delete_msg(context, chat_id, context.user_data["prompt_msg_id"])
    batch.append({"file_id": fid, "type": ftype, "text": txt})
    context.user_data["admin_files_batch"] = batch

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ اكتفيت بالمحتوى، انتقل لتحديد العنوان", callback_data="adm_done_files")], [InlineKeyboardButton("❌ إلغاء", callback_data="adm_cancel_op")]])
    new_msg = await msg.reply_text(f"📥 **تم استلام المادة ({len(batch)}/{MAX_BATCH_FILES})!** أرسل التالية أو اضغط الزر:", reply_markup=kb, parse_mode="Markdown")
    context.user_data["prompt_msg_id"] = new_msg.message_id
    return ADMIN_WAITING_FILES

async def admin_finish_files_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    if not context.user_data.get("admin_files_batch"):
        await safe_edit_text(query, "❌ لم ترسل أي مواد!", InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="adm_cancel_op")]])); return ADMIN_WAITING_FILES
    await safe_edit_text(query, f"✍️ أرسل الآن **الاسم/العنوان الظاهر للمجموعة**:", InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="adm_cancel_op")]]))
    return ADMIN_WAITING_TITLE

async def admin_save_file_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id, title = update.effective_chat.id, update.message.text.strip()
    await safe_delete_msg(context, chat_id, update.message.message_id)
    
    db = load_data()
    db[context.user_data["admin_target_cat"]][f"item_{int(update.message.date.timestamp())}"] = {"title": title, "files": context.user_data["admin_files_batch"]}
    save_data_instant(db); asyncio.create_task(asyncio.to_thread(sync_to_github))
    context.user_data.clear()

    await context.bot.send_message(chat_id, f"🎉 **تمت الإضافة بنجاح!**\n📌 **العنوان:** `{title}`", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« عودة للإدارة", callback_data="adm_main")]]), parse_mode="Markdown")
    return ConversationHandler.END

async def admin_del_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query, data, db = update.callback_query, update.callback_query.data, load_data()
    await query.answer()

    if data in ["adm_main", "adm_cancel_op"]: context.user_data.clear(); await show_admin_panel(update, context); return ConversationHandler.END
    if data == "adm_close": context.user_data.clear(); await safe_delete_msg(context, query.message.chat_id, query.message.message_id); return ConversationHandler.END
    
    op = "del" if "del" in data else "move"
    if data in ["adm_del_select_cat", "adm_move_select_cat"]:
        kb = [[InlineKeyboardButton(name, callback_data=f"adm{op}cat_{k}")] for k, name in CAT_NAMES.items()]
        kb.append([InlineKeyboardButton("« عودة للإدارة", callback_data="adm_main")])
        await safe_edit_text(query, f"اختر القسم لـ ({'الحذف' if op=='del' else 'النقل'}):", InlineKeyboardMarkup(kb))
    elif data.startswith(f"adm{op}cat_"):
        cat_key, items = data.replace(f"adm{op}cat_", ""), db.get(data.replace(f"adm{op}cat_", ""), {})
        if not items: await safe_edit_text(query, "⚠️ القسم فارغ.", InlineKeyboardMarkup([[InlineKeyboardButton("« عودة للإدارة", callback_data="adm_main")]])); return
        kb = [[InlineKeyboardButton(f"{'🗑️ حذف' if op=='del' else '📦 نقل'} ({idx})", callback_data=f"admdo{op}:{cat_key}:{iid}")] for idx, (iid, info) in enumerate(items.items(), 1)]
        kb.append([InlineKeyboardButton("« عودة للإدارة", callback_data="adm_main")])
        await safe_edit_text(query, "اختر المحتوى المطلوب:", InlineKeyboardMarkup(kb))
    elif data.startswith(f"admdo{op}:"):
        _, cat_key, iid = data.split(":", 2)
        if iid in db.get(cat_key, {}):
            if op == "del": del db[cat_key][iid]
            else: db.setdefault("general_archive", {})[iid] = db[cat_key].pop(iid)
            save_data_instant(db); asyncio.create_task(asyncio.to_thread(sync_to_github))
            await safe_edit_text(query, "✅ **تمت العملية بنجاح!**", InlineKeyboardMarkup([[InlineKeyboardButton("« عودة للإدارة", callback_data="adm_main")]]))

async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query: await update.callback_query.answer(); await safe_edit_text(update.callback_query, "🚫 **تم الإلغاء.**")
    else: await update.message.reply_text("🚫 **تم الإلغاء.**")
    context.user_data.clear(); return ConversationHandler.END

# --- تشغيل التطبيق ---
def main():
    threading.Thread(target=run_dummy_server, daemon=True).start()
    app = ApplicationBuilder().token(TOKEN).build()

    admin_conv = ConversationHandler(
        entry_points=[CommandHandler("admin", admin_command), CallbackQueryHandler(admin_add_start, pattern="^adm_add_start$")],
        states={
            ADMIN_PASSCODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_verify_passcode), CallbackQueryHandler(cancel_conv, pattern="^adm_cancel_op$")],
            ADMIN_SELECT_CAT: [CallbackQueryHandler(admin_select_cat_step, pattern="^admcat_"), CallbackQueryHandler(admin_del_handler, pattern="^(adm_main|adm_cancel_op)$")],
            ADMIN_WAITING_FILES: [MessageHandler((filters.Document.ALL | filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE | filters.TEXT) & ~filters.COMMAND, admin_receive_files_step), CallbackQueryHandler(admin_finish_files_step, pattern="^adm_done_files$"), CallbackQueryHandler(admin_del_handler, pattern="^adm_cancel_op$")],
            ADMIN_WAITING_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_save_file_step), CallbackQueryHandler(admin_del_handler, pattern="^adm_cancel_op$")]
        },
        fallbacks=[CommandHandler("cancel", cancel_conv), CallbackQueryHandler(cancel_conv, pattern="^cancel_conv$"), CallbackQueryHandler(admin_del_handler, pattern="^(adm_main|adm_cancel_op)$")],
        per_user=True
    )

    app.add_handler(admin_conv)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(admin_del_handler, pattern="^adm"))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("✅ HR Bot يعمل بأقصر وأجمل كود!")
    app.run_polling()

if __name__ == "__main__": main()

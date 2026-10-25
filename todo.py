"""📝 المهام اليومية — قسم قيد البناء.
عند البناء: نضيف الأزرار الفرعية هنا بنفس البادئة "todo_"،
وإن احتجنا حفظ بيانات نستخدم load_json/save_json من utils (ملف data/todo.json).
"""
from telegram import Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from utils import show_placeholder

TITLE = "📝 المهام اليومية"
MENU = "todo_menu"


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await show_placeholder(update.callback_query, TITLE)


def register(app) -> None:
    app.add_handler(CallbackQueryHandler(menu, pattern=f"^{MENU}$"))

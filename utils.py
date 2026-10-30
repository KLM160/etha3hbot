"""🧰 أدوات مساعدة مشتركة بين كل الأقسام."""
import json
import logging
import os
import threading
from typing import Any, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from config import DATA_DIR, ITEMS_PER_PAGE

logger = logging.getLogger("bot.utils")
_JSON_LOCK = threading.Lock()


# ----------------------------- تيليجرام -----------------------------
async def safe_delete_msg(context: ContextTypes.DEFAULT_TYPE, chat_id: int, msg_id: int) -> None:
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except TelegramError:
        pass


async def safe_edit_text(query, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None) -> None:
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            logger.warning("BadRequest عند تعديل رسالة: %s", e)


def grid(buttons: list[InlineKeyboardButton], cols: int = 2) -> list[list[InlineKeyboardButton]]:
    """يقسّم الأزرار صفوفًا (افتراضيًا ثنائي)."""
    return [buttons[i:i + cols] for i in range(0, len(buttons), cols)]


def home_button() -> InlineKeyboardButton:
    return InlineKeyboardButton("🏠 الرئيسية", callback_data="main")


async def show_placeholder(query, title: str) -> None:
    """شاشة مؤقتة للأقسام التي لم تُبنَ بعد."""
    await safe_edit_text(
        query,
        f"**{title}**\n━━━━━━━ • ✨ • ━━━━━━━\n\n🚧 هذا القسم قيد البناء، قريبًا بإذن الله.",
        InlineKeyboardMarkup([[home_button()]]),
    )


def build_paginated_keyboard(
    items: list[tuple[str, InlineKeyboardButton]],
    page: int,
    page_prefix: str,
    extra_rows: Optional[list[list[InlineKeyboardButton]]] = None,
) -> InlineKeyboardMarkup:
    """يبني كيبورد مقسّم على صفحات من قائمة (id, button)."""
    total_pages = max(1, (len(items) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * ITEMS_PER_PAGE
    rows = [[btn] for _, btn in items[start:start + ITEMS_PER_PAGE]]

    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️ السابق", callback_data=f"{page_prefix}:{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("التالي ▶️", callback_data=f"{page_prefix}:{page + 1}"))
        rows.append(nav)

    if extra_rows:
        rows.extend(r for r in extra_rows if r)
    return InlineKeyboardMarkup(rows)


# ------------------------ ملفات json للأقسام ------------------------
def load_json(filename: str, default: Any) -> Any:
    """يقرأ data/<filename>؛ يرجع نسخة من default إن لم يوجد الملف أو تلف."""
    path = os.path.join(DATA_DIR, filename)
    with _JSON_LOCK:
        if not os.path.exists(path):
            return json.loads(json.dumps(default))
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("JSON Error عند قراءة %s: %s", path, e)
            return json.loads(json.dumps(default))


def save_json(filename: str, data: Any) -> None:
    """يكتب data/<filename> كتابة ذرية."""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, filename)
    with _JSON_LOCK:
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        os.replace(tmp, path)


# ------------------------ شاشة التحكم الفعّالة ------------------------
PANEL_KEY = "panel_msg"


def track_panel(context: ContextTypes.DEFAULT_TYPE, message) -> None:
    """يحفظ (chat_id, message_id) لشاشة التحكم الحالية في bot_data (البوت لمالك واحد)."""
    try:
        context.application.bot_data[PANEL_KEY] = (message.chat.id, message.message_id)
    except AttributeError:
        pass


async def drop_old_panel(context: ContextTypes.DEFAULT_TYPE) -> None:
    """يحذف شاشة التحكم المحفوظة بصمت ويمسح المفتاح؛ لا يرمي استثناءً أبداً."""
    try:
        saved = context.application.bot_data.pop(PANEL_KEY, None)
        if saved:
            chat_id, msg_id = saved
            await safe_delete_msg(context, chat_id, msg_id)
    except Exception as e:  # noqa: BLE001
        logger.debug("تعذّر حذف الشاشة القديمة: %s", e)

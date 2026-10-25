"""🧰 أدوات مساعدة مشتركة بين كل الأقسام."""
import logging
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from config import ITEMS_PER_PAGE

logger = logging.getLogger("bot.utils")


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

"""🧰 أدوات مشتركة: تيليجرام، ملفات json، شاشة التحكم الفعّالة، ومزامنة GitHub."""
import base64
import json
import logging
import os
import threading
import time
from datetime import date, datetime
from typing import Any, Optional, Tuple
from zoneinfo import ZoneInfo

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, TelegramError
from telegram.ext import CallbackQueryHandler, ContextTypes

from config import DATA_DIR, GH_BRANCH, GH_DATA_DIR, GH_REPO, GH_TOKEN, ITEMS_PER_PAGE, TZ_NAME

logger = logging.getLogger("bot.utils")
_JSON_LOCK = threading.Lock()

# ----------------------------- التوقيت (الرياض) -----------------------------
# القاعدة: في الأقسام لا datetime.now() ولا date.today() مباشرة؛ استخدم الدوال التالية.
TZ = ZoneInfo(TZ_NAME)


def now() -> datetime:
    return datetime.now(TZ)


def today() -> date:
    return now().date()


def today_str() -> str:
    """YYYY-MM-DD"""
    return today().isoformat()


# ----------------------------- تيليجرام -----------------------------
async def safe_delete_msg(context: ContextTypes.DEFAULT_TYPE, chat_id: int, msg_id: int) -> None:
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except TelegramError:
        pass


async def safe_edit_text(query, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None) -> None:
    """يعدّل الرسالة بـ Markdown، وإن رفض تيليجرام التنسيق يعيد المحاولة نصًا عاديًا."""
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    except BadRequest as e:
        err = str(e).lower()
        if "not modified" in err:
            return
        if "parse entities" in err:
            try:
                await query.edit_message_text(text, reply_markup=reply_markup)
            except BadRequest as e2:
                if "not modified" not in str(e2).lower():
                    logger.warning("BadRequest عند تعديل رسالة: %s", e2)
            return
        logger.warning("BadRequest عند تعديل رسالة: %s", e)


def md_safe(text: str) -> str:
    """يحذف رموز Markdown التي تكسر الرسالة (للعناوين داخل النصوص)."""
    for ch in "*_`[":
        text = text.replace(ch, "")
    return text


def grid(buttons: list, cols: int = 2) -> list:
    return [buttons[i:i + cols] for i in range(0, len(buttons), cols)]


def home_button() -> InlineKeyboardButton:
    return InlineKeyboardButton("🏠 الرئيسية", callback_data="main")


def build_paginated_keyboard(buttons: list, page: int, page_prefix: str, extra_rows: Optional[list] = None) -> InlineKeyboardMarkup:
    """كيبورد مقسّم على صفحات من قائمة أزرار؛ أزرار التنقل تستدعي f"{page_prefix}:{رقم_الصفحة}"."""
    total_pages = max(1, (len(buttons) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * ITEMS_PER_PAGE
    rows = [[b] for b in buttons[start:start + ITEMS_PER_PAGE]]

    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️ السابق", callback_data=f"{page_prefix}:{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("التالي ▶️", callback_data=f"{page_prefix}:{page + 1}"))
        rows.append(nav)

    rows.extend(r for r in (extra_rows or []) if r)
    return InlineKeyboardMarkup(rows)


def register_placeholder(app, menu: str, title: str) -> None:
    """يسجّل شاشة «قيد البناء» لقسم لم يُبنَ بعد (يُستبدل ببناء القسم الفعلي لاحقًا)."""
    async def _menu(update, context):
        q = update.callback_query
        await q.answer()
        await safe_edit_text(
            q,
            f"**{title}**\n━━━━━━━ • ✨ • ━━━━━━━\n\n🚧 هذا القسم قيد البناء، قريبًا بإذن الله.",
            InlineKeyboardMarkup([[home_button()]]),
        )

    app.add_handler(CallbackQueryHandler(_menu, pattern=f"^{menu}$"))


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
        except Exception as e:  # noqa: BLE001
            logger.error("JSON Error عند قراءة %s: %s", path, e)
            return json.loads(json.dumps(default))


def save_json(filename: str, data: Any) -> None:
    """يكتب data/<filename> كتابة ذرية، ثم يرفعه إلى GitHub في الخلفية (إن كانت المزامنة مفعّلة)."""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, filename)
    with _JSON_LOCK:
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        os.replace(tmp, path)
    if github_enabled():
        threading.Thread(target=_push_data_file, args=(filename, path), daemon=True).start()


def _push_data_file(filename: str, local_path: str) -> None:
    """يرفع ملف data/<filename> إلى GitHub؛ أي فشل يُسجَّل في اللوج فقط."""
    try:
        ok, msg = github_push(local_path, f"{GH_DATA_DIR}/{filename}", f"💾 تحديث {filename} [skip render]")
        if not ok:
            logger.warning("تعذّر رفع %s إلى GitHub: %s", filename, msg)
    except Exception:  # noqa: BLE001
        logger.exception("خطأ غير متوقع عند رفع %s", filename)


# ------------------------ شاشة التحكم الفعّالة ------------------------
PANEL_KEY = "panel_msg"


def track_panel(context: ContextTypes.DEFAULT_TYPE, message) -> None:
    """يحفظ (chat_id, message_id) لشاشة التحكم الحالية في bot_data (البوت لمالك واحد)."""
    try:
        context.application.bot_data[PANEL_KEY] = (message.chat.id, message.message_id)
    except AttributeError:
        pass


async def drop_old_panel(context: ContextTypes.DEFAULT_TYPE) -> None:
    """يحذف شاشة التحكم المحفوظة بصمت؛ لا يرمي استثناءً أبداً."""
    try:
        saved = context.application.bot_data.pop(PANEL_KEY, None)
        if saved:
            await safe_delete_msg(context, saved[0], saved[1])
    except Exception as e:  # noqa: BLE001
        logger.debug("تعذّر حذف الشاشة القديمة: %s", e)


def claim_panel(context: ContextTypes.DEFAULT_TYPE, message) -> bool:
    """هل هذه الرسالة هي الواجهة الفعّالة؟ (إن لم تُسجَّل واجهة بعد إعادة التشغيل تصبح هذه هي الفعّالة)."""
    try:
        current = (message.chat.id, message.message_id)
    except AttributeError:
        return True
    saved = context.application.bot_data.get(PANEL_KEY)
    if saved is None:
        context.application.bot_data[PANEL_KEY] = current
        return True
    return tuple(saved) == current


# ------------------------ مزامنة GitHub ------------------------
# قرص Render مؤقت: نرفع كل تعديل إلى GitHub، ونجلب آخر نسخة عند بدء التشغيل.
_API = "https://api.github.com"
_PUSH_LOCK = threading.Lock()  # رفع واحد في كل مرة (يتفادى تعارض sha)


def github_enabled() -> bool:
    return bool(GH_TOKEN and GH_REPO)


def _gh_headers(raw: bool = False) -> dict:
    return {
        "Authorization": f"Bearer {GH_TOKEN}",
        "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _gh_url(repo_path: str) -> str:
    return f"{_API}/repos/{GH_REPO}/contents/{repo_path.lstrip('/')}"


def _gh_params() -> dict:
    return {"ref": GH_BRANCH} if GH_BRANCH else {}


def _gh_error(res: requests.Response) -> str:
    try:
        detail = res.json().get("message", "")
    except Exception:  # noqa: BLE001
        detail = res.text[:200]
    hints = {
        401: "التوكن غير صالح أو منتهي (GH_TOKEN).",
        403: "التوكن بلا صلاحية كتابة (يلزم Contents: Read and write) أو تجاوزت الحد.",
        404: "المستودع/الملف/الفرع غير موجود أو التوكن لا يراه (راجع GH_REPO و GH_BRANCH و GH_DATA_PATH).",
        409: "تعارض في النسخة.",
        422: "رفض GitHub الطلب (غالبًا الفرع أو sha).",
    }
    return f"HTTP {res.status_code} — {hints.get(res.status_code, '')} [{detail}]"


def github_pull(repo_path: str) -> Optional[bytes]:
    """يجلب محتوى ملف من GitHub، أو None إن لم يوجد/فشل."""
    if not github_enabled():
        return None
    try:
        res = requests.get(_gh_url(repo_path), headers=_gh_headers(raw=True), params=_gh_params(), timeout=20)
    except requests.RequestException as e:
        logger.error("تعذّر الاتصال بـ GitHub عند الجلب: %s", e)
        return None
    if res.status_code == 200:
        return res.content
    logger.error("فشل جلب %s من GitHub: %s", repo_path, _gh_error(res))
    return None


def github_push(local_path: str, repo_path: str, message: str) -> Tuple[bool, str]:
    """يرفع ملفًا محليًا إلى GitHub برسالة commit محددة. يرجع (نجح؟، رسالة)."""
    if not github_enabled():
        return False, "GH_TOKEN أو GH_REPO غير مضبوطين في متغيرات البيئة."

    with _PUSH_LOCK:
        last = "فشل غير معروف"
        for attempt in range(4):
            try:
                with open(local_path, "rb") as f:  # آخر نسخة في كل محاولة
                    content = base64.b64encode(f.read()).decode()

                get_res = requests.get(_gh_url(repo_path), headers=_gh_headers(), params=_gh_params(), timeout=20)
                if get_res.status_code == 200:
                    sha = get_res.json().get("sha")
                elif get_res.status_code == 404:
                    sha = None  # ملف جديد
                else:
                    last = f"عند قراءة الملف: {_gh_error(get_res)}"
                    logger.error("GitHub: %s", last)
                    return False, last

                payload = {"message": message, "content": content}
                if sha:
                    payload["sha"] = sha
                if GH_BRANCH:
                    payload["branch"] = GH_BRANCH

                put_res = requests.put(_gh_url(repo_path), headers=_gh_headers(), json=payload, timeout=30)
                if put_res.status_code in (200, 201):
                    logger.info("✅ GitHub: %s", message)
                    return True, "ok"

                last = f"عند الرفع: {_gh_error(put_res)}"
                if put_res.status_code in (409, 422) and attempt < 3:
                    time.sleep(1 + attempt)
                    continue
                logger.error("GitHub: %s", last)
                return False, last
            except requests.RequestException as e:
                last = f"خطأ شبكة: {e}"
                logger.error("GitHub: %s", last)
                if attempt < 3:
                    time.sleep(2)
                    continue
                return False, last
            except Exception as e:  # noqa: BLE001
                logger.exception("GitHub خطأ غير متوقع")
                return False, f"خطأ غير متوقع: {e}"
        return False, last


def github_list(repo_dir: str) -> list:
    """أسماء ملفات .json داخل مجلد في المستودع، أو [] مع تحذير عند أي فشل."""
    if not github_enabled():
        return []
    try:
        res = requests.get(_gh_url(repo_dir), headers=_gh_headers(), params=_gh_params(), timeout=20)
    except requests.RequestException as e:
        logger.warning("تعذّر الاتصال بـ GitHub عند سرد %s: %s", repo_dir, e)
        return []
    if res.status_code != 200:
        logger.warning("فشل سرد %s من GitHub: %s", repo_dir, _gh_error(res))
        return []
    try:
        items = res.json()
    except ValueError as e:
        logger.warning("ردّ غير متوقع عند سرد %s: %s", repo_dir, e)
        return []
    if not isinstance(items, list):
        logger.warning("%s في المستودع ليس مجلدًا.", repo_dir)
        return []
    return [i["name"] for i in items if i.get("type") == "file" and str(i.get("name", "")).endswith(".json")]


def restore_data_dir() -> None:
    """يجلب كل ملفات .json من GH_DATA_DIR إلى DATA_DIR (نسخة GitHub هي المرجع)؛ لا يحذف أي ملف محلي."""
    for name in github_list(GH_DATA_DIR):
        content = github_pull(f"{GH_DATA_DIR}/{name}")
        if content is None:
            continue  # تعذّر الجلب: تبقى النسخة المحلية إن وُجدت
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            path = os.path.join(DATA_DIR, name)
            with _JSON_LOCK:
                tmp = f"{path}.tmp"
                with open(tmp, "wb") as f:
                    f.write(content)
                os.replace(tmp, path)
            logger.info("✅ استُعيد %s من GitHub", name)
        except OSError as e:
            logger.error("تعذّرت استعادة %s: %s", name, e)

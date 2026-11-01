"""📝 المهام اليومية — قائمة مهام دائمة + تقييم ذاتي يومي من 20 + إحصائيات وأرشيف شهري.
--------------------------------------------------
- البيانات: data/todo.json عبر load_json/save_json (يرفع إلى GitHub تلقائيًا). التوقيت: الرياض فقط.
- كل callback_data يبدأ بـ todo_ (ما عدا main و noop العامين).
- التذكير اليومي 21:00 عبر app.job_queue داخل register(app).
- ترحيل يومي (توقيت الرياض): عند أول قراءة في يوم جديد (ومهمة 00:01) تُحذف المهام المكتملة في أيام سابقة،
  وتُحفظ أعدادها في done_stats لتبقى الإحصائيات دقيقة؛ وتبقى غير المنجزة وتنتقل لليوم الحالي (last_cleanup).
نقطة الربط مع main.py: TITLE و MENU و register(app).
"""
import logging
import re
from datetime import date, datetime, time, timedelta
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import ALLOWED_USER_ID, ITEMS_PER_PAGE
from utils import (
    TZ,
    build_paginated_keyboard,
    drop_old_panel,
    grid,
    home_button,
    load_json,
    md_safe,
    now,
    safe_delete_msg,
    safe_edit_text,
    save_json,
    today,
    today_str,
    track_panel,
)

logger = logging.getLogger("bot.todo")

# ----------------------------------------------------------------------------
# الثوابت
# ----------------------------------------------------------------------------
TITLE = "📝 المهام اليومية"
MENU = "todo_menu"

DATA_FILE = "todo.json"
RATING_START = date(2026, 11, 1)
MAX_SCORE = 20

MAX_TASKS = 50       # إجمالي المهام
MAX_TASK_LEN = 100   # أقصى طول للمهمة الواحدة
BTN_TEXT_LEN = 40    # طول نص المهمة على الزر
BAR_LEN = 10         # طول شريط التقدم
ARCHIVE_SHOW = 24    # أقصى عدد أشهر تُعرض في الأرشيف
REMINDER_TIME = time(21, 0, tzinfo=TZ)
ROLLOVER_TIME = time(0, 1, tzinfo=TZ)  # الترحيل اليومي بعد منتصف الليل

WAITING_TASKS = 0

# أسماء عربية بدون locale (مفتاح الأيام = date.weekday() حيث الاثنين = 0)
DAYS = {0: "الاثنين", 1: "الثلاثاء", 2: "الأربعاء", 3: "الخميس", 4: "الجمعة", 5: "السبت", 6: "الأحد"}
MONTHS = {
    1: "يناير", 2: "فبراير", 3: "مارس", 4: "أبريل", 5: "مايو", 6: "يونيو",
    7: "يوليو", 8: "أغسطس", 9: "سبتمبر", 10: "أكتوبر", 11: "نوفمبر", 12: "ديسمبر",
}

MONTH_KEY_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

STATS_BACK_ROW = [
    InlineKeyboardButton("🔙 الإحصائيات", callback_data="todo_stats"),
    InlineKeyboardButton("📝 المهام", callback_data=MENU),
]
STATS_BACK_KB = InlineKeyboardMarkup([STATS_BACK_ROW])


# ----------------------------------------------------------------------------
# أدوات عامة
# ----------------------------------------------------------------------------
def one_line(text) -> str:
    return " ".join(str(text).split())


def short(text, n: int) -> str:
    t = one_line(text)
    return t if len(t) <= n else t[: n - 1] + "…"


def parse_page(value) -> int:
    return int(value) if str(value).isdigit() else 0


def clamp_page(count: int, page: int) -> int:
    pages = max(1, (count + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    return max(0, min(page, pages - 1))


def parse_iso(value) -> Optional[date]:
    """YYYY-MM-DD صارم، أو None."""
    text = str(value)
    try:
        d = date.fromisoformat(text)
    except ValueError:
        return None
    return d if d.isoformat() == text else None


def fmt_date(d: date, with_year: bool = True) -> str:
    s = f"{DAYS[d.weekday()]} {d.day} {MONTHS[d.month]}"
    return f"{s} {d.year}" if with_year else s


def fmt_time(dt: datetime) -> str:
    """12 ساعة (ص/م)."""
    h = dt.hour % 12 or 12
    return f"{h}:{dt.minute:02d} {'ص' if dt.hour < 12 else 'م'}"


def tasks_word(n: int) -> str:
    if n == 1:
        return "مهمة واحدة"
    if n == 2:
        return "مهمتين"
    if 3 <= n <= 10:
        return f"{n} مهام"
    return f"{n} مهمة"


def pct_of(total: int, maximum: int) -> int:
    return int(total * 100 / maximum + 0.5) if maximum else 0


def bar(value: float, maximum: float, width: int = BAR_LEN) -> str:
    filled = 0 if maximum <= 0 else max(0, min(width, int(value / maximum * width + 0.5)))
    return "█" * filled + "░" * (width - filled)


def header(title: str, deco: str) -> str:
    return f"**{title}**\n━━━━━━━ • {deco} • ━━━━━━━\n\n"


def _arg(args: list, i: int, default: str = "") -> str:
    return args[i] if len(args) > i else default


async def _send_panel(context, chat_id: int, text: str, kb: InlineKeyboardMarkup):
    """يرسل شاشة جديدة ويسجّلها كواجهة فعّالة."""
    try:
        msg = await context.bot.send_message(chat_id, text, reply_markup=kb, parse_mode="Markdown")
    except BadRequest:
        msg = await context.bot.send_message(chat_id, text, reply_markup=kb)
    track_panel(context, msg)
    return msg


# ----------------------------------------------------------------------------
# البيانات
# ----------------------------------------------------------------------------
def _default() -> dict:
    return {"tasks": [], "ratings": {}, "monthly_archive": {}, "done_stats": {}, "last_cleanup": ""}


def _task_num(tid) -> int:
    m = re.fullmatch(r"t(\d+)", str(tid))
    return int(m.group(1)) if m else 0


def _normalize(raw) -> dict:
    """يطبّع البنية: لا ينهار على ملف ناقص أو تالف، ويصلح الحقول المعطوبة."""
    db = dict(raw) if isinstance(raw, dict) else {}

    # المهام
    raw_tasks = db.get("tasks")
    items = [t for t in raw_tasks if isinstance(t, dict)] if isinstance(raw_tasks, list) else []
    next_n = max([_task_num(t.get("id")) for t in items] + [0]) + 1
    seen, tasks = set(), []
    for t in items:
        text = one_line(t.get("text") or "")
        if not text:
            continue
        tid = str(t.get("id") or "").strip()
        if not tid or tid in seen:  # id مفقود أو مكرر
            tid = f"t{next_n}"
            next_n += 1
        seen.add(tid)
        done = bool(t.get("completed"))
        done_at = t.get("done_at")
        item = dict(t)
        item.update(
            id=tid,
            text=text,
            completed=done,
            created_at=str(t.get("created_at") or today_str()),
            done_at=done_at if done and parse_iso(done_at) else None,
        )
        tasks.append(item)
    db["tasks"] = tasks

    # التقييمات
    ratings = {}
    if isinstance(db.get("ratings"), dict):
        for k, v in db["ratings"].items():
            if parse_iso(k) is None or isinstance(v, bool):
                continue
            try:
                score = int(v)
            except (TypeError, ValueError):
                continue
            if 0 <= score <= MAX_SCORE:
                ratings[str(k)] = score
    db["ratings"] = ratings

    # الأرشيف الشهري
    archive = {}
    if isinstance(db.get("monthly_archive"), dict):
        for k, v in db["monthly_archive"].items():
            if not MONTH_KEY_RE.match(str(k)) or not isinstance(v, dict):
                continue
            try:
                archive[str(k)] = {
                    "total_score": int(v["total_score"]),
                    "max_score": int(v["max_score"]),
                    "days_count": int(v["days_count"]),
                }
            except (KeyError, TypeError, ValueError):
                continue
    db["monthly_archive"] = archive

    # إحصائيات المنجز اليومية (تبقى حتى بعد حذف المهام المكتملة)
    stats = {}
    if isinstance(db.get("done_stats"), dict):
        for k, v in db["done_stats"].items():
            if parse_iso(k) is None or isinstance(v, bool):
                continue
            try:
                n = int(v)
            except (TypeError, ValueError):
                continue
            if n > 0:
                stats[str(k)] = n
    db["done_stats"] = stats

    # تاريخ آخر تصفية/ترحيل
    last = db.get("last_cleanup")
    db["last_cleanup"] = str(last) if parse_iso(last) else ""
    return db


# ----------------------------------------------------------------------------
# الترحيل اليومي وتنظيف المهام المنجزة
# ----------------------------------------------------------------------------
_PENDING_NOTICE: list = []  # ملاحظة الترحيل تُعرض مرة واحدة في أول شاشة مهام


def _merge_notice(notice: str = "") -> str:
    """يدمج ملاحظة الترحيل المعلّقة (مرة واحدة) مع ملاحظة الشاشة."""
    parts = list(_PENDING_NOTICE)
    _PENDING_NOTICE.clear()
    if notice:
        parts.append(notice)
    return "\n".join(parts)


def _record_done(db: dict, tasks: list) -> int:
    """يضيف المهام المكتملة إلى done_stats[يوم الإنجاز] قبل حذفها. يرجع عدد ما سُجّل."""
    stats = db["done_stats"]
    n = 0
    for t in tasks:
        day = t.get("done_at")
        if t.get("completed") and parse_iso(day):
            stats[day] = stats.get(day, 0) + 1
            n += 1
    return n


def done_in_range(db: dict, lo: str, hi: str) -> int:
    """عدد المنجز بين تاريخين (شاملين): المحذوف المحفوظ في done_stats + المكتمل الظاهر في القائمة."""
    total = sum(v for k, v in db["done_stats"].items() if lo <= k <= hi)
    total += sum(1 for t in db["tasks"] if t["completed"] and t.get("done_at") and lo <= t["done_at"] <= hi)
    return total


def daily_cleanup(db: dict) -> bool:
    """إذا دخل يوم جديد (الرياض) بعد آخر تصفية: يحذف المكتملة من أيام سابقة مع حفظ إحصائيتها،
    وتبقى غير المنجزة (تنتقل لليوم الحالي)، ثم يحدّث last_cleanup. يرجع True إن تغيّر شيء."""
    today_iso = today_str()
    last = db.get("last_cleanup") or ""
    if last and today_iso <= last:
        return False

    kept, purged = [], []
    for t in db["tasks"]:
        if t["completed"] and not (t.get("done_at") and t["done_at"] >= today_iso):
            purged.append(t)  # أُنجزت قبل اليوم (أو بلا تاريخ إنجاز صالح)
        else:
            kept.append(t)

    _record_done(db, purged)
    carried = sum(1 for t in kept if not t["completed"] and str(t.get("created_at")) < today_iso)
    db["tasks"] = kept
    db["last_cleanup"] = today_iso

    parts = []
    if purged:
        parts.append(f"🧹 نُظّفت منجزات الأيام السابقة ({tasks_word(len(purged))}) وإحصائياتها محفوظة.")
    if carried:
        parts.append(f"🔄 رُحّل المتبقي إلى اليوم ({tasks_word(carried)}).")
    if parts:
        _PENDING_NOTICE.append("\n".join(parts))
    return True


def load_db() -> dict:
    """يقرأ البيانات ويطبّعها، ويجري الترحيل اليومي (ويحفظ) عند دخول يوم جديد."""
    db = _normalize(load_json(DATA_FILE, _default()))
    if daily_cleanup(db):
        save_db(db)
    return db


def save_db(db: dict) -> None:
    save_json(DATA_FILE, db)


def load_archived() -> dict:
    """يحمّل البيانات ويؤرشف الأشهر المنتهية إن لزم (يحفظ فقط عند وجود تغيير)."""
    db = load_db()
    if archive_closed_months(db):
        save_db(db)
    return db


def next_task_id(tasks: list) -> str:
    return f"t{max([_task_num(t['id']) for t in tasks] + [0]) + 1}"


def find_task(db: dict, tid: str) -> Optional[dict]:
    return next((t for t in db["tasks"] if t["id"] == tid), None)


# ----------------------------------------------------------------------------
# الأرشيف الشهري (غير هدّام: ratings اليومية تبقى)
# ----------------------------------------------------------------------------
def _month_entry(db: dict, month: str) -> Optional[dict]:
    scores = [v for d, v in db["ratings"].items() if d.startswith(month)]
    if not scores:
        return None
    return {"total_score": sum(scores), "max_score": len(scores) * MAX_SCORE, "days_count": len(scores)}


def archive_closed_months(db: dict) -> bool:
    """يؤرشف كل شهر أقدم من الحالي له تقييمات ولا يوجد في الأرشيف. يرجع True إن تغيّر شيء."""
    current = today_str()[:7]
    changed = False
    for month in sorted({d[:7] for d in db["ratings"]}):
        if month < current and month not in db["monthly_archive"]:
            entry = _month_entry(db, month)
            if entry:
                db["monthly_archive"][month] = entry
                changed = True
    return changed


def refresh_month_archive(db: dict, month: str) -> bool:
    """يعيد حساب أرشيف شهر سابق (عند تقييم متأخر). لا يلمس الشهر الحالي."""
    if month >= today_str()[:7]:
        return False
    entry = _month_entry(db, month)
    if entry is None or db["monthly_archive"].get(month) == entry:
        return False
    db["monthly_archive"][month] = entry
    return True


# ----------------------------------------------------------------------------
# التقييم: أيام معلّقة
# ----------------------------------------------------------------------------
def pending_dates(db: dict) -> list:
    """كل الأيام من RATING_START حتى أمس بلا تقييم، الأقدم أولًا."""
    t = today()
    out = []
    d = RATING_START
    while d < t:
        iso = d.isoformat()
        if iso not in db["ratings"]:
            out.append(iso)
        d += timedelta(days=1)
    return out


def before_start_sentence() -> str:
    """«يبدأ التقييم غداً الأحد 1 نوفمبر» — تُحسب من التاريخ ولا تُثبَّت."""
    delta = (RATING_START - today()).days
    with_year = RATING_START.year != today().year
    date_text = fmt_date(RATING_START, with_year)
    if delta == 1:
        return f"يبدأ التقييم غداً {date_text}"
    if delta == 2:
        return f"يبدأ التقييم بعد غد {date_text}"
    return f"يبدأ التقييم يوم {date_text} (بعد {delta} أيام)"


# ----------------------------------------------------------------------------
# شاشة المهام
# ----------------------------------------------------------------------------
def build_menu(db: dict, page: int = 0, notice: str = ""):
    tasks = db["tasks"]
    total = len(tasks)
    done = sum(1 for t in tasks if t["completed"])
    page = clamp_page(total, page)
    n = now()

    text = header(TITLE, "✨")
    if notice:
        text += notice + "\n\n"
    text += f"📅 {fmt_date(n.date())}\n🕘 {fmt_time(n)}\n\n"
    text += f"📊 الإجمالي: {total} • ✅ المكتملة: {done} • ⏳ المتبقية: {total - done}\n\n"
    text += "👇 اضغط على المهمة لتبديل حالتها:" if tasks else "🌱 لا توجد مهام بعد، اضغط «➕ إضافة مهمة» وابدأ يومك."

    buttons = [
        InlineKeyboardButton(
            f"{'✅' if t['completed'] else '⬜'} {short(t['text'], BTN_TEXT_LEN)}",
            callback_data=f"todo_t:{t['id']}:{page}",
        )
        for t in tasks
    ]
    extra = [
        [
            InlineKeyboardButton("➕ إضافة مهمة", callback_data="todo_add"),
            InlineKeyboardButton("🗑️ حذف", callback_data="todo_del:0"),
        ],
        [
            InlineKeyboardButton("⭐ التقييم اليومي", callback_data="todo_rate"),
            InlineKeyboardButton("📊 الإحصائيات والأرشيف", callback_data="todo_stats"),
        ],
        [home_button()],
    ]
    return text, build_paginated_keyboard(buttons, page, "todo_page", extra)


async def render_menu(query, page: int = 0, notice: str = "") -> None:
    db = load_archived()
    text, kb = build_menu(db, page, _merge_notice(notice))
    await safe_edit_text(query, text, kb)


async def send_menu(context, chat_id: int, notice: str = "") -> None:
    """drop_old_panel ثم شاشة القسم الجديدة ثم track_panel."""
    db = load_archived()
    text, kb = build_menu(db, 0, _merge_notice(notice))
    await drop_old_panel(context)
    await _send_panel(context, chat_id, text, kb)


# ----------------------------------------------------------------------------
# وضع الحذف
# ----------------------------------------------------------------------------
def build_delete(db: dict, page: int = 0, notice: str = ""):
    tasks = db["tasks"]
    page = clamp_page(len(tasks), page)
    done = sum(1 for t in tasks if t["completed"])

    text = header("🗑️ وضع الحذف", "🗑️")
    if notice:
        text += notice + "\n\n"
    if tasks:
        text += "👇 اضغط على المهمة التي تريد حذفها (سيُطلب منك التأكيد):"
    else:
        text += "🌱 لا توجد مهام للحذف."

    buttons = [
        InlineKeyboardButton(
            f"🗑️ {'✅' if t['completed'] else '⬜'} {short(t['text'], BTN_TEXT_LEN)}",
            callback_data=f"todo_d:{t['id']}:{page}",
        )
        for t in tasks
    ]
    extra = []
    if done:
        extra.append([InlineKeyboardButton(f"🧹 مسح المكتملة ({done})", callback_data="todo_cc")])
    extra.append([InlineKeyboardButton("🔙 رجوع للمهام", callback_data=MENU)])
    return text, build_paginated_keyboard(buttons, page, "todo_dpage", extra)


async def render_delete(query, page: int = 0, notice: str = "") -> None:
    text, kb = build_delete(load_db(), page, notice)
    await safe_edit_text(query, text, kb)


# ----------------------------------------------------------------------------
# التقييم الذاتي
# ----------------------------------------------------------------------------
def score_keyboard(day_iso: str, last_row: list) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(str(s), callback_data=f"todo_rs:{day_iso}:{s}")
        for s in range(MAX_SCORE + 1)
    ]
    return InlineKeyboardMarkup(grid(buttons, 5) + [last_row])


async def render_rating(query, notice: str = "", edit_day: Optional[str] = None) -> None:
    db = load_archived()
    today_iso = today_str()
    ratings = db["ratings"]
    back_menu = [InlineKeyboardButton("🔙 قائمة المهام", callback_data=MENU)]

    text = header("⭐ التقييم اليومي", "⭐")
    if notice:
        text += notice + "\n\n"

    # قبل بداية التقييم: لا يُحفظ شيء
    if today() < RATING_START:
        text += f"⏳ {before_start_sentence()}\n\n💪 جهّز نفسك لتقييم يومك من {MAX_SCORE} كل ليلة بإذن الله."
        await safe_edit_text(query, text, InlineKeyboardMarkup([back_menu]))
        return

    pending = pending_dates(db)
    editing = False
    if edit_day and edit_day == today_iso and edit_day in ratings:
        target, editing = edit_day, True
    elif pending:
        target = pending[0]
    elif today_iso not in ratings:
        target = today_iso
    else:
        target = None

    # اليوم مقيَّم ولا توجد أيام معلّقة
    if target is None:
        score = ratings[today_iso]
        text += (
            f"📅 {fmt_date(today())}\n"
            f"✅ **تقييم اليوم: {score}/{MAX_SCORE}**\n"
            f"{bar(score, MAX_SCORE)} {pct_of(score, MAX_SCORE)}%\n\n"
            "🌙 تم حفظ تقييمك، نلتقي غداً بإذن الله."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ تعديل", callback_data=f"todo_re:{today_iso}")],
            [InlineKeyboardButton("📊 الإحصائيات والأرشيف", callback_data="todo_stats")],
            back_menu,
        ])
        await safe_edit_text(query, text, kb)
        return

    # طلب درجة ليوم محدد
    label = fmt_date(date.fromisoformat(target)) + (" (اليوم)" if target == today_iso else "")
    text += f"📅 تقييم يوم: **{label}**\n"
    if editing:
        text += f"✏️ الدرجة الحالية: {ratings[target]}/{MAX_SCORE}\n"
    elif pending:
        text += f"📌 أيام سابقة غير مقيَّمة: {len(pending)}" + (" (نبدأ بالأقدم)" if len(pending) > 1 else "") + "\n"
    text += f"\nكم تعطي نفسك من {MAX_SCORE}؟ 👇"

    last_row = [InlineKeyboardButton("🔙 رجوع", callback_data="todo_rate")] if editing else back_menu
    await safe_edit_text(query, text, score_keyboard(target, last_row))


async def save_rating(query, day_iso: str, score_text: str) -> None:
    d = parse_iso(day_iso)
    if d is None or not score_text.isdigit() or not (0 <= int(score_text) <= MAX_SCORE):
        await render_rating(query, notice="⚠️ طلب غير صالح.")
        return
    score = int(score_text)
    if d < RATING_START or d > today():
        await render_rating(query, notice="⚠️ هذا التاريخ خارج نطاق التقييم.")
        return

    db = load_db()
    db["ratings"][day_iso] = score
    refresh_month_archive(db, day_iso[:7])  # تقييم متأخر لشهر سابق: أعد حساب أرشيفه
    archive_closed_months(db)
    save_db(db)
    await render_rating(query, notice=f"✅ تم حفظ تقييم {fmt_date(d, False)}: **{score}/{MAX_SCORE}**")


# ----------------------------------------------------------------------------
# الإحصائيات
# ----------------------------------------------------------------------------
def week_start_of(d: date) -> date:
    """بداية الأسبوع = الأحد."""
    return d - timedelta(days=(d.weekday() + 1) % 7)


def earliest_week(db: dict) -> date:
    candidates = [RATING_START]
    candidates += [d for d in (parse_iso(k) for k in db["ratings"]) if d]
    candidates += [d for d in (parse_iso(t.get("done_at")) for t in db["tasks"]) if d]
    candidates += [d for d in (parse_iso(k) for k in db["done_stats"]) if d]
    return min(week_start_of(min(candidates)), week_start_of(today()))


def build_stats_home():
    text = header("📊 الإحصائيات والأرشيف", "📊") + "اختر ما تريد عرضه 👇"
    buttons = [
        InlineKeyboardButton("📅 أسبوعية", callback_data=f"todo_sw:{week_start_of(today()).isoformat()}"),
        InlineKeyboardButton("📆 شهرية", callback_data="todo_sm"),
        InlineKeyboardButton("🗂️ الأرشيف", callback_data="todo_sa"),
    ]
    kb = InlineKeyboardMarkup(grid(buttons, 2) + [[InlineKeyboardButton("🔙 قائمة المهام", callback_data=MENU)]])
    return text, kb


def build_week(db: dict, start: date):
    cur, first = week_start_of(today()), earliest_week(db)
    start = max(first, min(week_start_of(start), cur))
    end = start + timedelta(days=6)
    ratings = db["ratings"]
    today_iso = today_str()

    text = header("📅 إحصائيات الأسبوع", "📊")
    text += f"🗓️ من {fmt_date(start, False)} إلى {fmt_date(end)}\n\n"

    days = [start + timedelta(days=i) for i in range(7)]
    week_scores = [ratings[d.isoformat()] for d in days if d.isoformat() in ratings]

    if week_scores:
        for d in days:
            iso = d.isoformat()
            label = f"{DAYS[d.weekday()]} {d.day}/{d.month}"
            mark = " 👈" if iso == today_iso else ""
            if iso in ratings:
                text += f"• {label}: {bar(ratings[iso], MAX_SCORE)} {ratings[iso]}/{MAX_SCORE}{mark}\n"
            else:
                text += f"• {label}: —{mark}\n"
        avg = sum(week_scores) / len(week_scores)
        text += f"\n📈 المتوسط: {avg:.1f}/{MAX_SCORE} ({len(week_scores)} أيام مقيَّمة)\n"
    elif today() < RATING_START:
        text += f"⏳ {before_start_sentence()}، وستظهر درجاتك هنا من ذلك اليوم.\n"
    else:
        text += "🌱 لا توجد تقييمات في هذا الأسبوع بعد.\n"

    s_iso, e_iso = start.isoformat(), end.isoformat()
    done_count = done_in_range(db, s_iso, e_iso)
    text += f"✅ المهام المنجزة: {done_count}"

    nav = []
    if start > first:
        nav.append(InlineKeyboardButton("◀️ السابق", callback_data=f"todo_sw:{(start - timedelta(days=7)).isoformat()}"))
    if start < cur:
        nav.append(InlineKeyboardButton("التالي ▶️", callback_data=f"todo_sw:{(start + timedelta(days=7)).isoformat()}"))
    rows = ([nav] if nav else []) + [STATS_BACK_ROW]
    return text, InlineKeyboardMarkup(rows)


def build_month(db: dict):
    t = today()
    ratings = db["ratings"]
    text = header("📆 إحصائيات الشهر", "📊")

    if t < RATING_START:
        return text + f"⏳ {before_start_sentence()}، وستظهر إحصائيات الشهر بعدها.", STATS_BACK_KB

    month_key = f"{t.year}-{t.month:02d}"
    month_done = done_in_range(db, f"{month_key}-01", f"{month_key}-31")
    first = max(date(t.year, t.month, 1), RATING_START)
    # الأيام المنقضية: حتى أمس، ويُحتسب اليوم فقط إذا قُيِّم
    last = t if t.isoformat() in ratings else t - timedelta(days=1)
    missing = []
    d = first
    while d <= last:
        if d.isoformat() not in ratings:
            missing.append(d.day)
        d += timedelta(days=1)
    elapsed = max(0, (last - first).days + 1)

    rated = sorted(
        (k, v) for k, v in ratings.items() if k.startswith(month_key) and k >= RATING_START.isoformat()
    )
    text += f"🗓️ {MONTHS[t.month]} {t.year}\n\n"

    if not rated:
        text += "🌱 لا توجد تقييمات هذا الشهر بعد."
        text += f"\n✅ المهام المنجزة: {month_done}"
        if missing:
            text += f"\n⚠️ أيام بلا تقييم: {len(missing)}"
        return text, STATS_BACK_KB

    total = sum(v for _, v in rated)
    maximum = len(rated) * MAX_SCORE
    best = max(rated, key=lambda kv: kv[1])   # أول يوم بين المتساوين
    worst = min(rated, key=lambda kv: kv[1])

    text += (
        f"✅ الأيام المقيَّمة: {len(rated)} من {elapsed} منقضية\n"
        f"🎯 المجموع: {total}/{maximum}\n"
        f"{bar(total, maximum)} {pct_of(total, maximum)}%\n"
        f"📈 المتوسط: {total / len(rated):.1f}/{MAX_SCORE}\n"
        f"✅ المهام المنجزة: {month_done}\n"
        f"🏆 أفضل يوم: {fmt_date(date.fromisoformat(best[0]), False)} ({best[1]}/{MAX_SCORE})\n"
        f"📉 أسوأ يوم: {fmt_date(date.fromisoformat(worst[0]), False)} ({worst[1]}/{MAX_SCORE})\n"
    )
    if missing:
        days_text = "، ".join(str(x) for x in missing[:10]) + (" …" if len(missing) > 10 else "")
        text += f"⚠️ أيام بلا تقييم: {len(missing)} ({days_text})"
    else:
        text += "🎉 لا توجد أيام بلا تقييم، استمر!"
    return text, STATS_BACK_KB


def build_archive(db: dict):
    archive = db["monthly_archive"]
    text = header("🗂️ الأرشيف الشهري", "📊")
    if not archive:
        text += "🌱 لا يوجد أرشيف بعد.\nيُؤرشف كل شهر تلقائياً بعد انتهائه، وسيظهر هنا ملخص أشهرك السابقة."
        return text, STATS_BACK_KB

    keys = sorted(archive, reverse=True)  # الأحدث أولًا
    for k in keys[:ARCHIVE_SHOW]:
        e = archive[k]
        done_n = done_in_range(db, f"{k}-01", f"{k}-31")
        done_txt = f" • ✅ {done_n}" if done_n else ""
        text += f"📆 {k} ({MONTHS[int(k[5:])]}): {e['total_score']}/{e['max_score']} • {pct_of(e['total_score'], e['max_score'])}%{done_txt}\n"
    if len(keys) > ARCHIVE_SHOW:
        text += f"\n… و{len(keys) - ARCHIVE_SHOW} أشهر أقدم"
    return text.rstrip(), STATS_BACK_KB


# ----------------------------------------------------------------------------
# معالج الأزرار العام
# ----------------------------------------------------------------------------
async def todo_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cmd, _, rest = (query.data or "").partition(":")
    args = rest.split(":") if rest else []

    if cmd == "todo_menu":
        await render_menu(query, 0)

    elif cmd == "todo_page":
        await render_menu(query, parse_page(_arg(args, 0)))

    elif cmd == "todo_t":  # تبديل حالة مهمة: todo_t:<id>:<page>
        page = parse_page(_arg(args, 1))
        db = load_db()
        task = find_task(db, _arg(args, 0))
        if task is None:
            await render_menu(query, page, "⚠️ هذه المهمة لم تعد موجودة.")
            return
        task["completed"] = not task["completed"]
        task["done_at"] = today_str() if task["completed"] else None
        save_db(db)
        await render_menu(query, page)

    elif cmd in ("todo_del", "todo_dpage"):
        await render_delete(query, parse_page(_arg(args, 0)))

    elif cmd == "todo_d":  # تأكيد حذف مهمة: todo_d:<id>:<page>
        page = parse_page(_arg(args, 1))
        task = find_task(load_db(), _arg(args, 0))
        if task is None:
            await render_delete(query, page, "⚠️ هذه المهمة لم تعد موجودة.")
            return
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ نعم، احذف", callback_data=f"todo_dy:{task['id']}:{page}"),
            InlineKeyboardButton("❌ تراجع", callback_data=f"todo_dpage:{page}"),
        ]])
        await safe_edit_text(
            query,
            f"⚠️ **تأكيد الحذف**\n\nهل تريد حذف المهمة «{md_safe(short(task['text'], 60))}» نهائياً؟",
            kb,
        )

    elif cmd == "todo_dy":  # حذف نهائي
        page = parse_page(_arg(args, 1))
        db = load_db()
        task = find_task(db, _arg(args, 0))
        if task is None:
            await render_delete(query, page, "⚠️ هذه المهمة لم تعد موجودة.")
            return
        _record_done(db, [task])  # إن كانت مكتملة تبقى في الإحصائيات
        db["tasks"] = [t for t in db["tasks"] if t["id"] != task["id"]]
        save_db(db)
        notice = f"🗑️ تم حذف: {md_safe(short(task['text'], 60))}"
        if db["tasks"]:
            await render_delete(query, page, notice)
        else:
            await render_menu(query, 0, notice)

    elif cmd == "todo_cc":  # تأكيد مسح المكتملة
        count = sum(1 for t in load_db()["tasks"] if t["completed"])
        if count == 0:
            await render_delete(query, 0, "ℹ️ لا توجد مهام مكتملة لمسحها.")
            return
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ نعم، امسح", callback_data="todo_ccy"),
            InlineKeyboardButton("❌ تراجع", callback_data="todo_dpage:0"),
        ]])
        await safe_edit_text(
            query,
            f"🧹 **مسح المكتملة**\n\nسيتم حذف {tasks_word(count)} مكتملة نهائياً. هل أنت متأكد؟",
            kb,
        )

    elif cmd == "todo_ccy":  # مسح المكتملة
        db = load_db()
        count = sum(1 for t in db["tasks"] if t["completed"])
        if count:
            _record_done(db, [t for t in db["tasks"] if t["completed"]])
            db["tasks"] = [t for t in db["tasks"] if not t["completed"]]
            save_db(db)
        await render_menu(query, 0, f"🧹 تم مسح {tasks_word(count)} مكتملة." if count else "ℹ️ لا توجد مهام مكتملة لمسحها.")

    elif cmd == "todo_rate":
        await render_rating(query)

    elif cmd == "todo_re":  # تعديل تقييم اليوم
        await render_rating(query, edit_day=_arg(args, 0))

    elif cmd == "todo_rs":  # حفظ تقييم: todo_rs:<YYYY-MM-DD>:<score>
        await save_rating(query, _arg(args, 0), _arg(args, 1))

    elif cmd == "todo_stats":
        text, kb = build_stats_home()
        await safe_edit_text(query, text, kb)

    elif cmd == "todo_sw":
        start = parse_iso(_arg(args, 0)) or today()
        text, kb = build_week(load_archived(), start)
        await safe_edit_text(query, text, kb)

    elif cmd == "todo_sm":
        text, kb = build_month(load_archived())
        await safe_edit_text(query, text, kb)

    elif cmd == "todo_sa":
        text, kb = build_archive(load_archived())
        await safe_edit_text(query, text, kb)


# ----------------------------------------------------------------------------
# الإضافة (محادثة) — تبدأ من زر «➕ إضافة مهمة»
# ----------------------------------------------------------------------------
CANCEL_KB = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="todo_cancel")]])


def add_prompt_text(room: int, notice: str = "") -> str:
    text = header("➕ إضافة مهام", "📝")
    if notice:
        text += notice + "\n\n"
    text += (
        "✍️ أرسل مهمتك الآن.\n"
        "💡 لإضافة عدة مهام أرسلها في رسالة واحدة، كل سطر مهمة.\n\n"
        f"📏 أقصى طول للمهمة: {MAX_TASK_LEN} حرف\n"
        f"📦 المتاح: {room} من {MAX_TASKS}"
    )
    return text


async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    room = MAX_TASKS - len(load_db()["tasks"])
    if room <= 0:
        await query.answer(f"وصلت للحد الأقصى ({MAX_TASKS} مهمة). احذف بعضها أولاً.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    await safe_edit_text(query, add_prompt_text(room), CANCEL_KB)
    return WAITING_TASKS


async def add_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    raw = update.message.text or ""
    await safe_delete_msg(context, chat_id, update.message.message_id)

    db = load_db()
    room = MAX_TASKS - len(db["tasks"])
    if room <= 0:
        context.user_data.clear()
        await send_menu(context, chat_id, f"⚠️ وصلت للحد الأقصى ({MAX_TASKS} مهمة).")
        return ConversationHandler.END

    lines = [l for l in (one_line(x) for x in raw.splitlines()) if l]
    accepted, too_long, over_limit = [], 0, 0
    for line in lines:
        if len(line) > MAX_TASK_LEN:
            too_long += 1
        elif len(accepted) >= room:
            over_limit += 1
        else:
            accepted.append(line)

    warnings = []
    if too_long:
        warnings.append(f"⚠️ تم تجاهل {too_long} سطر لتجاوزه {MAX_TASK_LEN} حرف.")
    if over_limit:
        warnings.append(f"⚠️ تم تجاهل {over_limit} بسبب الوصول للحد الأقصى ({MAX_TASKS} مهمة).")

    if not accepted:  # لا شيء صالح: نعيد طلب الإدخال بنفس الواجهة
        warnings.insert(0, "⚠️ لم أجد مهمة صالحة، أعد المحاولة.")
        await drop_old_panel(context)
        await _send_panel(context, chat_id, add_prompt_text(room, "\n".join(warnings)), CANCEL_KB)
        return WAITING_TASKS

    today_iso = today_str()
    for line in accepted:
        db["tasks"].append({
            "id": next_task_id(db["tasks"]),
            "text": line,
            "completed": False,
            "created_at": today_iso,
            "done_at": None,
        })
    save_db(db)  # حفظ واحد لكل الدفعة
    context.user_data.clear()

    notice = "\n".join([f"✅ تمت إضافة {tasks_word(len(accepted))}."] + warnings)
    await send_menu(context, chat_id, notice)
    return ConversationHandler.END


async def add_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await render_menu(query, 0)
    return ConversationHandler.END


async def add_cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await safe_delete_msg(context, chat_id, update.message.message_id)
    context.user_data.clear()
    await send_menu(context, chat_id, "🚫 تم الإلغاء.")
    return ConversationHandler.END


async def end_on_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start أثناء الإضافة: ينهي المحادثة بصمت (الرد تتولاه start في main.py عبر group=1)."""
    context.user_data.clear()
    return ConversationHandler.END


async def rate_from_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """زر «قيّم الآن» من التذكير أثناء محادثة الإضافة: ننهيها وننتقل للتقييم."""
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await render_rating(query)
    return ConversationHandler.END


async def stale_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """زر إلغاء عالق بعد إعادة تشغيل البوت (المحادثة لم تعد موجودة)."""
    query = update.callback_query
    await query.answer("انتهت الجلسة")
    context.user_data.clear()
    await render_menu(query, 0)


# ----------------------------------------------------------------------------
# التذكير اليومي 21:00 (الرياض)
# ----------------------------------------------------------------------------
def days_word(n: int) -> str:
    if n == 1:
        return "يوم واحد"
    if n == 2:
        return "يومان"
    if 3 <= n <= 10:
        return f"{n} أيام"
    return f"{n} يوماً"


async def rating_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        db = load_db()
        if archive_closed_months(db):
            save_db(db)
        if today() < RATING_START:
            return
        pending = pending_dates(db)
        today_rated = today_str() in db["ratings"]
        if today_rated and not pending:
            return

        text = header("⭐ تذكير التقييم اليومي", "⭐")
        if not today_rated:
            text += f"🌙 حان وقت تقييم يومك من {MAX_SCORE}!\n"
        if pending:
            text += f"📌 أيام سابقة غير مقيَّمة: {days_word(len(pending))}\n"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⭐ قيّم الآن", callback_data="todo_rate")]])

        await drop_old_panel(context)
        await _send_panel(context, ALLOWED_USER_ID, text.rstrip(), kb)  # التتبع داخل _send_panel
    except TelegramError as e:
        logger.warning("تعذّر إرسال تذكير التقييم: %s", e)
    except Exception:  # noqa: BLE001
        logger.exception("خطأ غير متوقع في تذكير التقييم")


async def daily_rollover(context: ContextTypes.DEFAULT_TYPE) -> None:
    """00:01 الرياض: يجري الترحيل/التنظيف دون انتظار فتح القائمة (load_db يحفظ عند دخول يوم جديد)."""
    try:
        load_archived()
    except Exception:  # noqa: BLE001
        logger.exception("خطأ غير متوقع في الترحيل اليومي")


# ----------------------------------------------------------------------------
# الربط مع main.py
# ----------------------------------------------------------------------------
ROUTER_PATTERN = (
    r"^todo_(menu$|page:|t:|del:|dpage:|d:|dy:|cc$|ccy$|rate$|re:|rs:|stats$|sw:|sm$|sa$)"
)


def register(app) -> None:
    add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_start, pattern=r"^todo_add$")],
        states={
            WAITING_TASKS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_receive),
                CallbackQueryHandler(rate_from_add, pattern=r"^todo_rate$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", add_cancel_cmd),
            CommandHandler("start", end_on_start),
            CallbackQueryHandler(add_cancel, pattern=r"^todo_cancel$"),
        ],
        per_user=True,
    )
    app.add_handler(add_conv)
    app.add_handler(CallbackQueryHandler(stale_cancel, pattern=r"^todo_cancel$"))
    app.add_handler(CallbackQueryHandler(todo_router, pattern=ROUTER_PATTERN))

    if app.job_queue is None:
        logger.warning("⚠️ job_queue غير متاح (ثبّت python-telegram-bot[job-queue]): لن يعمل تذكير التقييم 21:00.")
    else:
        app.job_queue.run_daily(rating_reminder, time=REMINDER_TIME, name="todo_rating_reminder")
        app.job_queue.run_daily(daily_rollover, time=ROLLOVER_TIME, name="todo_daily_rollover")

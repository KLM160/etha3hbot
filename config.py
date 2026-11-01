"""⚙️ الإعدادات المشتركة لكل أقسام البوت (كلها من متغيرات البيئة في Render)."""
import os

TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    raise ValueError("❌ خطأ: لم يتم العثور على التوكن! تأكد من ضبط متغير البيئة TOKEN.")

# ---- GitHub: حفظ بيانات البوت دائمًا (قرص Render مؤقت) ----
GH_TOKEN = (os.environ.get("GH_TOKEN") or "").strip() or None


def _clean_repo(value):
    """يقبل owner/repo أو رابط المستودع الكامل."""
    value = (value or "").strip()
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    value = value.strip("/")
    if value.endswith(".git"):
        value = value[:-4]
    return value or None


GH_REPO = _clean_repo(os.environ.get("GH_REPO"))                      # owner/repo
GH_BRANCH = (os.environ.get("GH_BRANCH") or "").strip() or None       # فارغ = الفرع الافتراضي
GH_DATA_PATH = (os.environ.get("GH_DATA_PATH") or "data/radio.json").strip().lstrip("/")  # مسار radio.json في المستودع
GH_DATA_DIR = (os.environ.get("GH_DATA_DIR") or "data").strip().strip("/")

# 🔒 البوت خاص بالمالك فقط
ALLOWED_USER_ID = 8912887919
OWNER_NAME = "حامد"

ITEMS_PER_PAGE = 8  # عدد الأزرار في كل صفحة من القوائم
DATA_DIR = "data"   # ملفات json للأقسام الجديدة (الإذاعة تستخدم data/radio.json)
TZ_NAME = os.environ.get("TZ_NAME", "Asia/Riyadh")

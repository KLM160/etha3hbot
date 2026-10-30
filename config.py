"""⚙️ الإعدادات المشتركة لكل أقسام البوت."""
import os

TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    raise ValueError("❌ خطأ: لم يتم العثور على التوكن! تأكد من ضبط متغير البيئة TOKEN.")

GH_TOKEN = (os.environ.get("GH_TOKEN") or "").strip() or None


def _clean_repo(value):
    """يقبل owner/repo أو رابط المستودع الكامل ويحوّله إلى owner/repo."""
    value = (value or "").strip()
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    value = value.strip("/")
    if value.endswith(".git"):
        value = value[:-4]
    return value or None


GH_REPO = _clean_repo(os.environ.get("GH_REPO"))
# الفرع الذي ينشر منه Render (اتركه فارغًا لاستخدام الفرع الافتراضي)
GH_BRANCH = (os.environ.get("GH_BRANCH") or "").strip() or None
# مسار data.json داخل المستودع (غيّره إذا كان الملف داخل مجلد، مثل bot/data.json)
GH_DATA_PATH = (os.environ.get("GH_DATA_PATH") or "data.json").strip().lstrip("/")

# 🔒 البوت خاص بالمالك فقط
ALLOWED_USER_ID = 8912887919
OWNER_NAME = "حامد"

ITEMS_PER_PAGE = 8  # عدد الأزرار في كل صفحة من القوائم
DATA_DIR = "data"   # مجلد ملفات json الخاصة بالأقسام الجديدة (الإذاعة تبقى على data.json)

"""⚙️ الإعدادات المشتركة لكل أقسام البوت."""
import os

TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    raise ValueError("❌ خطأ: لم يتم العثور على التوكن! تأكد من ضبط متغير البيئة TOKEN.")

GH_TOKEN = os.environ.get("GH_TOKEN")
GH_REPO = os.environ.get("GH_REPO")

# 🔒 البوت خاص بالمالك فقط
ALLOWED_USER_ID = 8912887919
OWNER_NAME = "حامد"

ITEMS_PER_PAGE = 8  # عدد الأزرار في كل صفحة من القوائم
DATA_DIR = "data"   # مجلد ملفات json الخاصة بالأقسام الجديدة (الإذاعة تبقى على data.json)

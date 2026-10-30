"""📖 القرآن الكريم — قسم قيد البناء.
عند البناء: نستبدل register_placeholder بأزرار القسم الفعلية بنفس البادئة "quran_"،
وللحفظ نستخدم load_json/save_json من utils (ملف data/quran.json).
"""
from utils import register_placeholder

TITLE = "📖 القرآن الكريم"
MENU = "quran_menu"


def register(app) -> None:
    register_placeholder(app, MENU, TITLE)

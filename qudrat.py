"""🎯 القدرات — قسم قيد البناء.
عند البناء: نستبدل register_placeholder بأزرار القسم الفعلية بنفس البادئة "qudrat_"،
وللحفظ نستخدم load_json/save_json من utils (ملف data/qudrat.json).
"""
from utils import register_placeholder

TITLE = "🎯 القدرات"
MENU = "qudrat_menu"


def register(app) -> None:
    register_placeholder(app, MENU, TITLE)

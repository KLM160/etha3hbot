"""📝 المهام اليومية — قسم قيد البناء.
عند البناء: نستبدل register_placeholder بأزرار القسم الفعلية بنفس البادئة "todo_"،
وللحفظ نستخدم load_json/save_json من utils (ملف data/todo.json).
"""
from utils import register_placeholder

TITLE = "📝 المهام اليومية"
MENU = "todo_menu"


def register(app) -> None:
    register_placeholder(app, MENU, TITLE)

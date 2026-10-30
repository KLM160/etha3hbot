"""🏋️ التمرين — قسم قيد البناء.
عند البناء: نستبدل register_placeholder بأزرار القسم الفعلية بنفس البادئة "workout_"،
وللحفظ نستخدم load_json/save_json من utils (ملف data/workout.json).
"""
from utils import register_placeholder

TITLE = "🏋️ التمرين"
MENU = "workout_menu"


def register(app) -> None:
    register_placeholder(app, MENU, TITLE)

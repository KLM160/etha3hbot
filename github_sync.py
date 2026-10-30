"""🔄 مزامنة ملفات المشروع مع GitHub (رفع وجلب) — تُستخدم من radio.py وأي قسم يحتاج حفظًا دائمًا.

لماذا؟ قرص Render مؤقت: عند كل إعادة تشغيل يرجع البوت لنسخة الملفات الموجودة وقت النشر.
لذلك: نرفع كل تعديل إلى GitHub، ونجلب آخر نسخة من GitHub عند بدء التشغيل.
"""
import logging
import threading
import time
from typing import Optional, Tuple

import requests

from config import GH_BRANCH, GH_REPO, GH_TOKEN

logger = logging.getLogger("bot.github")
API = "https://api.github.com"
_PUSH_LOCK = threading.Lock()  # رفع واحد في كل مرة (يتفادى تعارض sha)


def enabled() -> bool:
    return bool(GH_TOKEN and GH_REPO)


def _headers(raw: bool = False) -> dict:
    return {
        "Authorization": f"Bearer {GH_TOKEN}",
        "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _url(repo_path: str) -> str:
    return f"{API}/repos/{GH_REPO}/contents/{repo_path.lstrip('/')}"


def _params() -> dict:
    return {"ref": GH_BRANCH} if GH_BRANCH else {}


def _err(res: requests.Response) -> str:
    try:
        detail = res.json().get("message", "")
    except Exception:  # noqa: BLE001
        detail = res.text[:200]
    hints = {
        401: "التوكن غير صالح أو منتهي (GH_TOKEN).",
        403: "التوكن لا يملك صلاحية الكتابة (يلزم Contents: Read and write) أو تجاوزت الحد.",
        404: "المستودع أو الملف أو الفرع غير موجود، أو التوكن لا يرى المستودع (تحقق من GH_REPO / GH_BRANCH / GH_DATA_PATH).",
        409: "تعارض في النسخة.",
        422: "رفض GitHub الطلب (غالبًا الفرع أو sha).",
    }
    return f"HTTP {res.status_code} — {hints.get(res.status_code, '')} [{detail}]"


def pull_file(repo_path: str) -> Optional[bytes]:
    """يجلب محتوى الملف من GitHub، أو None إن لم يوجد/فشل."""
    if not enabled():
        return None
    try:
        res = requests.get(_url(repo_path), headers=_headers(raw=True), params=_params(), timeout=20)
    except requests.RequestException as e:
        logger.error("تعذّر الاتصال بـ GitHub عند الجلب: %s", e)
        return None
    if res.status_code == 200:
        return res.content
    logger.error("فشل جلب %s من GitHub: %s", repo_path, _err(res))
    return None


def push_file(local_path: str, repo_path: str, message: str) -> Tuple[bool, str]:
    """يرفع ملفًا محليًا إلى GitHub. يرجع (نجح؟، رسالة)."""
    if not enabled():
        return False, "GH_TOKEN أو GH_REPO غير مضبوطين في متغيرات البيئة."

    import base64

    with _PUSH_LOCK:
        last = "فشل غير معروف"
        for attempt in range(4):
            try:
                with open(local_path, "rb") as f:  # آخر نسخة في كل محاولة
                    content = base64.b64encode(f.read()).decode()

                get_res = requests.get(_url(repo_path), headers=_headers(), params=_params(), timeout=20)
                if get_res.status_code == 200:
                    sha = get_res.json().get("sha")
                elif get_res.status_code == 404:
                    sha = None  # ملف جديد
                else:
                    last = f"عند قراءة الملف: {_err(get_res)}"
                    logger.error("GitHub: %s", last)
                    return False, last

                payload = {"message": message, "content": content}
                if sha:
                    payload["sha"] = sha
                if GH_BRANCH:
                    payload["branch"] = GH_BRANCH

                put_res = requests.put(_url(repo_path), headers=_headers(), json=payload, timeout=30)
                if put_res.status_code in (200, 201):
                    logger.info("✅ تمت مزامنة %s مع GitHub.", repo_path)
                    return True, "ok"

                last = f"عند الرفع: {_err(put_res)}"
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

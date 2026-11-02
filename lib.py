"""کدهای مشترک collect.py و bot_ping.py: ارتباط با Worker، لاگ امن و فهرست حساب‌های تلگرام."""
import os, json, hashlib, urllib.request, urllib.error

WORKER_URL = os.environ["WORKER_URL"].rstrip("/")
WORKER_KEY = os.environ["WORKER_KEY"]
MAX_ACCOUNTS = 10          # TG_SESSION, TG_SESSION_2 ... TG_SESSION_10


class WorkerError(Exception):
    pass


def describe(e):
    """پیام خطای Worker (امن برای لاگ public) یا فقط نوع خطا."""
    return str(e) if isinstance(e, WorkerError) else type(e).__name__


def env(name, default=""):
    return (os.environ.get(name) or "").strip() or default


def tag(s):
    """شناسه‌ی کوتاه برای لاگ (اسم کانال تو لاگ public چاپ نمی‌شه)."""
    return "#" + hashlib.sha1(s.lower().encode()).hexdigest()[:6]


def api(path, payload, timeout=90):
    req = urllib.request.Request(
        WORKER_URL + path, data=json.dumps(payload).encode(),
        headers={"content-type": "application/json", "x-api-key": WORKER_KEY, "user-agent": "iwana-collector/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise WorkerError(f"HTTP {e.code} {e.read(200).decode('utf-8', 'ignore').strip()}") from None
    except urllib.error.URLError as e:
        raise WorkerError(f"اتصال به Worker ممکن نیست: {str(e.reason)[:80]}") from None


def accounts():
    """[(شماره, session)] از Secret های TG_SESSION و TG_SESSION_2 تا TG_SESSION_10 (تکراری‌ها حذف می‌شن)."""
    out, seen = [], set()
    for i in range(1, MAX_ACCOUNTS + 1):
        v = env("TG_SESSION" if i == 1 else f"TG_SESSION_{i}")
        if v and v not in seen:
            seen.add(v)
            out.append((i, v))
    return out

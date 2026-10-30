# هر ~۶۵ دقیقه یه پیام برای ربات می‌فرسته، کانفیگ جوابش رو اسم‌گذاری می‌کنه و تو Iwana.txt می‌ذاره.
# تنظیمات از Secret ها میاد: BOT_USERNAME, BOT_TEXT (اختیاری: BOT_INTERVAL, NEW_NAME, OUTPUT_FILE).
# وضعیت تو KV کلادفلر می‌مونه.
import os, re, sys, json, time, base64, asyncio, pathlib, urllib.request, urllib.error
from urllib.parse import quote


class WorkerError(Exception):
    pass


def describe(e):
    """پیام خطای Worker (امن برای لاگ public) یا فقط نوع خطا."""
    return str(e) if isinstance(e, WorkerError) else type(e).__name__

ROOT = pathlib.Path(__file__).parent
WORKER_URL = os.environ["WORKER_URL"].rstrip("/")
WORKER_KEY = os.environ["WORKER_KEY"]

def env(name, default=""):
    return (os.environ.get(name) or "").strip() or default


BOT = env("BOT_USERNAME").lstrip("@")
TEXT = env("BOT_TEXT", "/start")
NAME = env("NEW_NAME", "@I_w_a_n_a")
try:
    INTERVAL = max(15, int(env("BOT_INTERVAL", "65"))) * 60
except ValueError:
    INTERVAL = 65 * 60
_of = env("OUTPUT_FILE", "Iwana.txt")
if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}\.txt", _of):
    sys.exit("output_file نامعتبره (فقط اسم ساده با پسوند .txt)")
OUTFILE = ROOT / _of
CAP = 500

CFG_RE = re.compile(r'(?:vless|vmess|trojan|ss|hysteria2|hy2|tuic)://[^\s<>"\'`]+', re.I)


def api(path, payload):
    req = urllib.request.Request(
        WORKER_URL + path, data=json.dumps(payload).encode(),
        headers={"content-type": "application/json", "x-api-key": WORKER_KEY, "user-agent": "iwana-collector/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise WorkerError(f"HTTP {e.code} {e.read(200).decode('utf-8', 'ignore').strip()}") from None
    except urllib.error.URLError as e:
        raise WorkerError(f"اتصال به Worker ممکن نیست: {str(e.reason)[:80]}") from None


def rename(link):
    link = link.strip().rstrip(".,;)]}»")
    if link.lower().startswith("vmess://"):
        body = link[8:].split("#")[0]
        try:
            j = json.loads(base64.b64decode(body + "=" * (-len(body) % 4)).decode("utf-8"))
            j["ps"] = NAME
            raw = json.dumps(j, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            return "vmess://" + base64.b64encode(raw).decode()
        except Exception:
            return None
    return link.split("#")[0] + "#" + quote(NAME, safe="")


def due(state):
    now = time.time()
    return (now - state.get("last_ok", 0) >= INTERVAL - 90) and (now - state.get("last_try", 0) >= 900)


async def run():
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.tl.types import MessageEntityTextUrl

    async with TelegramClient(StringSession(os.environ["TG_SESSION"]),
                              int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"]) as c:
        peer = await c.get_input_entity(BOT)      # هر بار پیدا می‌شه؛ چیزی از ربات ذخیره نمی‌شه
        sent = await c.send_message(peer, TEXT)
        found = []
        for _ in range(8):                        # تا ~۴۰ ثانیه منتظر جواب می‌مونه
            await asyncio.sleep(5)
            async for m in c.iter_messages(peer, min_id=sent.id, limit=10):
                if m.out:
                    continue
                t = m.raw_text or ""
                for e in (m.entities or []):
                    if isinstance(e, MessageEntityTextUrl):
                        t += "\n" + e.url
                found += CFG_RE.findall(t)
            if found:
                break
    return found


def main():
    if not BOT:
        print("Secret به اسم BOT_USERNAME تنظیم نشده")
        return
    try:
        state = api("/state", {"name": "bot"}).get("data") or {}
    except Exception as e:
        print(f"خوندن وضعیت از Worker شکست خورد: {describe(e)}")
        return                                    # بدون وضعیت چیزی نمی‌فرستیم
    if not due(state):
        print("هنوز وقتش نشده")
        return
    state["last_try"] = time.time()
    try:
        api("/state", {"name": "bot", "data": state})
    except Exception as e:
        print(f"ذخیره‌ی وضعیت شکست خورد: {describe(e)}")
        return
    try:
        found = asyncio.run(run())
    except Exception as e:
        print(f"ارسال به ربات شکست خورد: {type(e).__name__}")
        return

    new = [r for r in (rename(x) for x in found) if r]
    if not new:
        print("کانفیگی تو جواب ربات پیدا نشد")
        return
    old = OUTFILE.read_text(encoding="utf-8").splitlines() if OUTFILE.exists() else []
    merged = []
    for l in new + old:                           # تازه‌ترین‌ها بالا
        if l and l not in merged:
            merged.append(l)
    OUTFILE.write_text("\n".join(merged[:CAP]) + "\n", encoding="utf-8")
    state["last_ok"] = time.time()
    try:
        api("/state", {"name": "bot", "data": state})
    except Exception as e:
        print(f"ذخیره‌ی وضعیت شکست خورد: {describe(e)}")
    print(f"{len(new)} کانفیگ ذخیره شد")


main()

# هر ۱۵ دقیقه (GitHub Actions). هیچ فایلی تو ریپو نوشته نمی‌شه.
# لیست کانال‌ها از Secret های CHANNELS_CFG / CHANNELS_IR / CHANNELS_RU میاد (هر خط یا هر فاصله یک کانال).
#  cfg -> کانال‌های کانفیگ (متن + فایل قفل‌دار)  -> Telegram.txt
#  ir  -> پروکسی ایرانی -> ته هر خط: فاصله + ⭐
#  ru  -> پروکسی روسی   -> ته هر خط: فاصله + ru
# وضعیت (آخرین پیام خونده‌شده) تو KV کلادفلر می‌مونه و لاگ‌ها اسم کانال ندارن.
import os, re, json, html, base64, asyncio, random, pathlib, sys, hashlib, urllib.request
from urllib.parse import parse_qs
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError
from telethon.tl.types import InputPeerChannel, MessageEntityTextUrl

WORKER_URL = os.environ["WORKER_URL"].rstrip("/")
WORKER_KEY = os.environ["WORKER_KEY"]

MAX_FILES = 15
SUFFIX = {"ir": " ⭐", "ru": " ru"}
NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{3,31}")

CFG_RE = re.compile(r'(?:vless|vmess|trojan|ss|hysteria2|hy2|tuic)://[^\s<>"\'`]+', re.I)
PROXY_RE = re.compile(
    r'(?:https?://)?(?:t|telegram)\.(?:me|dog)/(proxy|socks)\?[^\s<>"\'`]+|tg://(proxy|socks)\?[^\s<>"\'`]+', re.I)
LOCKED = {".npvt", ".npvs", ".hat", ".nm", ".dark", ".slip", ".tls", ".bpf", ".happ"}
TEXT = {".txt", ".json", ".yaml", ".yml"}


def tag(s):
    """شناسه‌ی کوتاه برای لاگ (اسم کانال تو لاگ public چاپ نمی‌شه)."""
    return "#" + hashlib.sha1(s.lower().encode()).hexdigest()[:6]


def clean(s):
    return s.rstrip(".,;)]}»،")


def extract_configs(text):
    return [clean(x) for x in CFG_RE.findall(text)]


def norm_proxies(text):
    """هر شکلی از لینک پروکسی رو به tg:// تبدیل می‌کنه."""
    out = []
    for m in PROXY_RE.finditer(text):
        kind = (m.group(1) or m.group(2)).lower()
        q = clean(html.unescape(m.group(0).split("?", 1)[1]))
        qs = parse_qs(q)
        if not qs.get("server") or not qs.get("port") or not qs["port"][0].isdigit():
            continue
        if kind == "proxy" and not qs.get("secret"):
            continue
        out.append(f"tg://{kind}?{q}")
    return out


def gather(msg):
    """متن پیام + لینک کلمه‌های لینک‌دار + لینک دکمه‌های شیشه‌ای."""
    parts = [msg.raw_text or ""]
    for e in (msg.entities or []):
        if isinstance(e, MessageEntityTextUrl):
            parts.append(e.url)
    for row in (msg.buttons or []):
        for b in row:
            u = getattr(b, "url", None)
            if u:
                parts.append(u)
    return "\n".join(parts)


ENV_OF = {"cfg": "CHANNELS_CFG", "ir": "CHANNELS_IR", "ru": "CHANNELS_RU"}


def read_list(key):
    """یوزرنیم، @یوزرنیم یا لینک t.me/... ؛ جداشده با خط جدید، فاصله یا ویرگول."""
    out = []
    for tok in re.split(r"[\s,،;]+", os.environ.get(ENV_OF[key]) or ""):
        if not tok:
            continue
        m = re.search(r"(?:t|telegram)\.me/(?:s/)?([A-Za-z][A-Za-z0-9_]{3,31})(?:[/?]|$)", tok, re.I)
        n = m.group(1) if m else tok.lstrip("@")
        if NAME_RE.fullmatch(n):
            out.append(n)
    return list(dict.fromkeys(out))


def api(path, payload):
    req = urllib.request.Request(
        WORKER_URL + path, data=json.dumps(payload).encode(),
        headers={"content-type": "application/json", "x-api-key": WORKER_KEY, "user-agent": "iwana-collector/1.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read())


async def get_channel(client, src, cache):
    if src in cache:
        i, h = cache[src]
        return InputPeerChannel(i, h)
    ent = await client.get_entity(src)
    cache[src] = [ent.id, ent.access_hash]
    await asyncio.sleep(random.uniform(3, 6))
    return InputPeerChannel(ent.id, ent.access_hash)


async def main():
    roles = {}
    for role in ("cfg", "ir", "ru"):
        for n in read_list(role):
            roles.setdefault(n.lower(), set()).add(role)
    if not roles:
        print("هیچ کانالی تنظیم نشده (Secret های CHANNELS_CFG / CHANNELS_IR / CHANNELS_RU رو پر کن)")
        return

    try:
        state = (await asyncio.to_thread(api, "/state", {"name": "collect"})).get("data") or {}
    except Exception as e:
        print(f"خوندن وضعیت از Worker شکست خورد: {type(e).__name__}")
        sys.exit(1)
    state.setdefault("last", {}); state.setdefault("peers", {})

    configs, proxies, new_last, n_files = [], [], {}, 0

    async with TelegramClient(StringSession(os.environ["TG_SESSION"]),
                              int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"]) as client:
        for src, rs in roles.items():
            try:
                peer = await get_channel(client, src, state["peers"])
                last = state["last"].get(src, 0)
                incremental = bool(last)
                it = (client.iter_messages(peer, min_id=last, reverse=True, limit=500) if incremental
                      else client.iter_messages(peer, limit=100))
                max_id = last
                async for msg in it:
                    max_id = max(max_id, msg.id)
                    blob = gather(msg)

                    if rs & {"ir", "ru"}:
                        found = norm_proxies(blob)
                        for role in ("ir", "ru"):
                            if role in rs:
                                proxies += [p + SUFFIX[role] for p in found]

                    if "cfg" not in rs:
                        continue
                    configs += extract_configs(blob)

                    f = msg.file
                    if not (f and f.name and (f.size or 0) < 1_000_000):
                        continue
                    ext = pathlib.Path(f.name).suffix.lower()
                    if ext in LOCKED:
                        if n_files >= MAX_FILES:
                            if incremental:
                                max_id = msg.id - 1
                                break
                            continue
                        data = await msg.download_media(file=bytes)      # فقط تو حافظه
                        n_files += 1
                        r = await asyncio.to_thread(api, "/convert",
                                                    {"name": f.name, "b64": base64.b64encode(data).decode()})
                        configs += r.get("links", [])
                        if r.get("needs_password"):
                            print(f"رمز داره، رد شد: {tag(src)}/{msg.id}")
                        elif r.get("error"):
                            print(f"تبدیل نشد {tag(src)}/{msg.id}: {r['error']}")
                    elif ext in TEXT:
                        data = await msg.download_media(file=bytes)
                        configs += extract_configs(data.decode("utf-8", errors="ignore"))
                new_last[src] = max_id
            except FloodWaitError as e:
                print(f"FloodWait {e.seconds}s on {tag(src)}")
                if e.seconds > 60:
                    break
                await asyncio.sleep(e.seconds + 2)
            except Exception as e:
                print(f"skip {tag(src)}: {type(e).__name__}")
            await asyncio.sleep(random.uniform(2, 5))

    configs = list(dict.fromkeys(configs))
    proxies = list(dict.fromkeys(proxies))
    ok = True
    try:
        for kind, lines in (("configs", configs), ("proxies", proxies)):
            if lines:
                r = await asyncio.to_thread(api, "/push", {"kind": kind, "lines": lines})
                print(f"{kind}: {r.get('added', 0)} جدید از {r.get('received', 0)}")
    except Exception as e:
        ok = False
        print(f"push failed: {type(e).__name__}")
    if ok:
        state["last"].update(new_last)
    try:
        await asyncio.to_thread(api, "/state", {"name": "collect", "data": state})
    except Exception as e:
        print(f"ذخیره‌ی وضعیت شکست خورد: {type(e).__name__}")
        ok = False
    if not ok:
        sys.exit(1)


asyncio.run(main())

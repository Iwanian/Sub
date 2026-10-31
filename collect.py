# هر ۱۵ دقیقه (GitHub Actions). هیچ فایلی تو ریپو نوشته نمی‌شه.
# لیست کانال‌ها از Secret های CHANNELS_CFG / CHANNELS_IR / CHANNELS_RU میاد (هر خط یا هر فاصله یک کانال).
#  cfg -> کانال‌های کانفیگ (متن + فایل قفل‌دار)  -> Telegram.txt
#  ir  -> پروکسی ایرانی -> ته هر خط: فاصله + ⭐
#  ru  -> پروکسی روسی   -> ته هر خط: فاصله + ru
# وضعیت (آخرین پیام خونده‌شده) تو KV کلادفلر می‌مونه و لاگ‌ها اسم کانال ندارن.
import os, re, json, html, base64, asyncio, random, pathlib, sys, hashlib, urllib.request, urllib.error
from urllib.parse import parse_qs
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, ChannelInvalidError, PeerIdInvalidError
from telethon.tl.types import Channel, InputPeerChannel, MessageEntityTextUrl

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


class WorkerError(Exception):
    pass


def describe(e):
    """پیام خطای Worker (امن برای لاگ public) یا فقط نوع خطا."""
    return str(e) if isinstance(e, WorkerError) else type(e).__name__


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
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise WorkerError(f"HTTP {e.code} {e.read(200).decode('utf-8', 'ignore').strip()}") from None
    except urllib.error.URLError as e:
        raise WorkerError(f"اتصال به Worker ممکن نیست: {str(e.reason)[:80]}") from None


async def get_channel(client, src, cache, fresh=False):
    """شناسه‌ی کانال از کش؛ اگه کش نبود (یا fresh) از تلگرام پیدا می‌شه."""
    if not fresh and src in cache:
        i, h = cache[src]
        return InputPeerChannel(i, h)
    ent = await client.get_entity(src)
    if not isinstance(ent, Channel):
        raise TypeError("not_a_channel")        # یوزرنیم مال آدم یا ربات بود، نه کانال/گروه
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
        print(f"خوندن وضعیت از Worker شکست خورد: {describe(e)}")
        sys.exit(1)
    state.setdefault("last", {}); state.setdefault("peers", {})
    # شناسه‌ی کانال‌ها به سشن وابسته‌ست؛ اگه TG_SESSION عوض شده باشه کش باطل می‌شه
    fp = hashlib.sha1(os.environ["TG_SESSION"].encode()).hexdigest()[:8]
    if state.get("fp") != fp:
        state["peers"] = {}
        state["fp"] = fp

    configs, proxies, new_last, n_files = [], [], {}, 0
    stats = {"ok": 0, "msgs": 0, "healed": 0}
    errs = []

    async with TelegramClient(StringSession(os.environ["TG_SESSION"]),
                              int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"]) as client:
        for src, rs in roles.items():
            try:
                for attempt in (0, 1):
                    try:
                        peer = await get_channel(client, src, state["peers"], fresh=bool(attempt))
                        last = state["last"].get(src, 0)
                        incremental = bool(last)
                        it = (client.iter_messages(peer, min_id=last, reverse=True, limit=500) if incremental
                              else client.iter_messages(peer, limit=100))
                        max_id = last
                        async for msg in it:
                            max_id = max(max_id, msg.id)
                            stats["msgs"] += 1
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
                        stats["ok"] += 1
                        break
                    except (ChannelInvalidError, PeerIdInvalidError):
                        if attempt == 1:
                            raise
                        state["peers"].pop(src, None)      # کش خراب بود؛ یک بار دوباره پیدا می‌کنیم
                        stats["healed"] += 1
            except FloodWaitError as e:
                print(f"FloodWait {e.seconds}s on {tag(src)}")
                errs.append("FloodWait")
                if e.seconds > 60:
                    break
                await asyncio.sleep(e.seconds + 2)
            except Exception as e:
                print(f"skip {tag(src)}: {type(e).__name__}")
                errs.append(type(e).__name__)
            await asyncio.sleep(random.uniform(2, 5))

    configs = list(dict.fromkeys(configs))
    proxies = list(dict.fromkeys(proxies))
    print(f"خلاصه: کانال موفق {stats['ok']}/{len(roles)}، پیام جدید {stats['msgs']}، "
          f"کانفیگ {len(configs)}، پروکسی {len(proxies)}، فایل قفل‌دار {n_files}"
          + (f"، کش بازسازی‌شده {stats['healed']}" if stats["healed"] else "")
          + (f"، خطاها: {sorted(set(errs))}" if errs else ""))
    ok = True
    try:
        for kind, lines in (("configs", configs), ("proxies", proxies)):
            if lines:
                r = await asyncio.to_thread(api, "/push", {"kind": kind, "lines": lines})
                print(f"{kind}: {r.get('added', 0)} جدید از {r.get('received', 0)}")
    except Exception as e:
        ok = False
        print(f"push failed: {describe(e)}")
    if ok:
        state["last"].update(new_last)
    try:
        await asyncio.to_thread(api, "/state", {"name": "collect", "data": state})
    except Exception as e:
        print(f"ذخیره‌ی وضعیت شکست خورد: {describe(e)}")
        ok = False
    if not ok:
        sys.exit(1)


asyncio.run(main())

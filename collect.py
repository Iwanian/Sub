# هر ۱۵ دقیقه (GitHub Actions). هیچ فایلی ذخیره نمی‌شه.
#  channels.txt  -> کانال‌های کانفیگ  (متن + فایل‌های قفل‌دار) -> Telegram.txt
#  proxy_ir.txt  -> کانال‌های پروکسی ایرانی -> ته هر خط: فاصله + ⭐
#  proxy_ru.txt  -> کانال‌های پروکسی روسی   -> ته هر خط: فاصله + ru
import os, re, json, html, base64, asyncio, random, pathlib, sys, urllib.request
from urllib.parse import parse_qs
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError
from telethon.tl.types import InputPeerChannel, MessageEntityTextUrl

ROOT = pathlib.Path(__file__).parent
STATE = ROOT / "state.json"
WORKER_URL = os.environ["WORKER_URL"].rstrip("/")
WORKER_KEY = os.environ["WORKER_KEY"]

MAX_FILES = 15
SUFFIX = {"ir": " ⭐", "ru": " ru"}   # یک فاصله + علامت

CFG_RE = re.compile(r'(?:vless|vmess|trojan|ss|hysteria2|hy2|tuic)://[^\s<>"\'`]+', re.I)
PROXY_RE = re.compile(
    r'(?:https?://)?(?:t|telegram)\.(?:me|dog)/(proxy|socks)\?[^\s<>"\'`]+|tg://(proxy|socks)\?[^\s<>"\'`]+', re.I)
LOCKED = {".npvt", ".npvs", ".hat", ".nm", ".dark", ".slip", ".tls", ".bpf", ".happ"}
TEXT = {".txt", ".json", ".yaml", ".yml"}


def clean(s):
    return s.rstrip(".,;)]}»،")


def extract_configs(text):
    return [clean(x) for x in CFG_RE.findall(text)]


def norm_proxies(text):
    """هر شکلی از لینک پروکسی (https://t.me/proxy?... ، tg://proxy?... ، socks) رو به tg:// تبدیل می‌کنه."""
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


def read_list(name):
    p = ROOT / name
    if not p.exists():
        return []
    return [l.strip().lstrip("@") for l in p.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.strip().startswith("#")]


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
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    state.setdefault("last", {}); state.setdefault("peers", {})

    # هر کانال فقط یک بار خونده می‌شه، حتی اگه تو چند لیست باشه
    roles = {}
    for role, fname in (("cfg", "channels.txt"), ("ir", "proxy_ir.txt"), ("ru", "proxy_ru.txt")):
        for n in read_list(fname):
            roles.setdefault(n.lower(), set()).add(role)

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
                            print(f"رمز داره، رد شد: {src}/{msg.id}")
                        elif r.get("error"):
                            print(f"تبدیل نشد {src}/{msg.id}: {r['error']}")
                    elif ext in TEXT:
                        data = await msg.download_media(file=bytes)
                        configs += extract_configs(data.decode("utf-8", errors="ignore"))
                new_last[src] = max_id
            except FloodWaitError as e:
                print(f"FloodWait {e.seconds}s on {src}")
                if e.seconds > 60:
                    break
                await asyncio.sleep(e.seconds + 2)
            except Exception as e:
                print(f"skip {src}: {e}")
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
        print(f"push failed: {e}")
    if ok:
        state["last"].update(new_last)
    STATE.write_text(json.dumps(state))
    if not ok:
        sys.exit(1)


asyncio.run(main())

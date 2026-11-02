# هر ۱۵ دقیقه (GitHub Actions). هیچ فایلی تو ریپو نوشته نمی‌شه.
# لیست کانال‌ها از Secret های CHANNELS_CFG / CHANNELS_IR / CHANNELS_RU میاد (هر خط یا هر فاصله یک کانال).
#  cfg -> کانال‌های کانفیگ (متن + فایل قفل‌دار)  -> Telegram.txt
#  ir  -> پروکسی ایرانی -> ته هر خط: فاصله + ⭐
#  ru  -> پروکسی روسی   -> ته هر خط: فاصله + ru
# وضعیت (آخرین پیام خونده‌شده) تو KV کلادفلر می‌مونه و لاگ‌ها اسم کانال ندارن.
import os, re, json, html, time, base64, asyncio, random, pathlib, sys, hashlib, datetime, urllib.request, urllib.error
from urllib.parse import parse_qs, urlsplit
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, ChannelInvalidError, PeerIdInvalidError
from telethon.tl.types import Channel, InputPeerChannel, MessageEntityTextUrl

from lib import api, describe, tag, accounts

MAX_FILES = 15
SUFFIX = {"ir": " ⭐", "ru": " ru"}
NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{3,31}")

SEEN_CAP = 25000          # تعداد اثرانگشت مواردِ دیده‌شده که تو KV نگه داشته می‌شه
STATS_DAYS = 7            # امتیاز کانال‌ها از روی چند روز اخیر
MAX_LINE = 2000           # خط بلندتر از این اسپم حساب می‌شه
# آدرس‌های ساختگی/نمونه: اسپم حساب می‌شن (فیلتر امنیتی یا کیفیت نیست)
PLACEHOLDER_HOSTS = {"example.com", "example.org", "domain.com", "yourdomain.com", "your.domain.com",
                     "localhost", "0.0.0.0", "127.0.0.1", "::1"}

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


def host_of_config(link):
    try:
        if link.lower().startswith("vmess://"):
            body = link[8:].split("#")[0]
            return str(json.loads(base64.b64decode(body + "=" * (-len(body) % 4)))["add"]).lower()
        return (urlsplit(link).hostname or "").lower()
    except Exception:
        return ""


def is_spam_config(link):
    return len(link) > MAX_LINE or host_of_config(link) in PLACEHOLDER_HOSTS


def is_spam_proxy(base):
    qs = parse_qs(base.split("?", 1)[1])
    return len(base) > MAX_LINE or (qs.get("server") or [""])[0].lower() in PLACEHOLDER_HOSTS


def canon_config(link):
    """کلید تکراری‌یابی: اسم (#remark) نادیده گرفته می‌شه و ترتیب پارامترها مهم نیست."""
    l = link.strip()
    if l.lower().startswith("vmess://"):
        body = l[8:].split("#")[0]
        try:
            j = json.loads(base64.b64decode(body + "=" * (-len(body) % 4)).decode("utf-8", "ignore"))
            j.pop("ps", None)
            return "vmess|" + json.dumps(j, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        except Exception:
            return l.split("#")[0]
    sp = urlsplit(l.split("#")[0])
    userinfo, _, hostport = sp.netloc.rpartition("@")
    q = "&".join(sorted(p for p in sp.query.split("&") if p))
    return f"{sp.scheme.lower()}://{userinfo}@{hostport.lower()}{sp.path}?{q}"


def canon_proxy(base):
    kind = base.split("://", 1)[1].split("?", 1)[0].lower()
    qs = parse_qs(base.split("?", 1)[1])
    g = lambda k: (qs.get(k) or [""])[0]
    return f"{kind}|{g('server').lower()}|{g('port')}|{g('secret').lower()}|{g('user')}|{g('pass')}"


def fp_of(key):
    return hashlib.sha1(key.encode()).hexdigest()[:10]


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
    state.setdefault("seen", []); state.setdefault("stats", {}); state.setdefault("roles", {})
    # شناسه‌ی کانال‌ها به سشن وابسته‌ست؛ اگه TG_SESSION عوض شده باشه کش باطل می‌شه
    accs = accounts()
    if not accs:
        print("هیچ Secret ای به اسم TG_SESSION تنظیم نشده")
        sys.exit(1)
    session = accs[0][1]          # خوندن کانال‌ها همیشه با اولین حساب است
    fp = hashlib.sha1(session.encode()).hexdigest()[:8]
    if state.get("fp") != fp:
        state["peers"] = {}
        state["fp"] = fp

    records, msgs_by_src = [], {}      # records: (نوع، کانال، خط، کلید، اسپم؟، زمان پیام)
    new_last, n_files = {}, 0
    stats = {"ok": 0, "msgs": 0, "healed": 0}
    errs = []

    def add_cfg(src, line, ts):
        records.append(("cfg", src, line, "cfg|" + canon_config(line), is_spam_config(line), ts))

    def add_prox(src, role, base, ts):
        records.append(("prox", src, base + SUFFIX[role], role + "|" + canon_proxy(base), is_spam_proxy(base), ts))

    async with TelegramClient(StringSession(session),
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
                            msgs_by_src[src] = msgs_by_src.get(src, 0) + 1
                            ts = msg.date.timestamp() if getattr(msg, "date", None) else time.time()
                            blob = gather(msg)

                            if rs & {"ir", "ru"}:
                                found = norm_proxies(blob)
                                for role in ("ir", "ru"):
                                    if role in rs:
                                        for p in found:
                                            add_prox(src, role, p, ts)

                            if "cfg" not in rs:
                                continue
                            for l in extract_configs(blob):
                                add_cfg(src, l, ts)

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
                                for l in r.get("links", []):
                                    add_cfg(src, l, ts)
                                if r.get("needs_password"):
                                    print(f"رمز داره، رد شد: {tag(src)}/{msg.id}")
                                elif r.get("error"):
                                    print(f"تبدیل نشد {tag(src)}/{msg.id}: {r['error']}")
                            elif ext in TEXT:
                                data = await msg.download_media(file=bytes)
                                for l in extract_configs(data.decode("utf-8", errors="ignore")):
                                    add_cfg(src, l, ts)
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

    # فقط کانال‌هایی که کامل خونده شدن حساب می‌شن (بقیه دفعه‌ی بعد دوباره خونده می‌شن)
    records = [r for r in records if r[1] in new_last]
    records.sort(key=lambda r: r[5])             # قدیمی‌ترین پیام اول: اعتبار «یکتا» می‌ره به منبع اصلی

    seen = set(state["seen"])
    new_hashes, new_cfg, new_prox = [], [], []
    day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    delta = {src: [msgs_by_src.get(src, 0), 0, 0, 0, 0] for src in new_last}   # پیام، مورد، یکتا، اسپم، تکراری
    for kind, src, line, key, spam, _ts in records:
        d = delta[src]
        d[1] += 1
        if spam:
            d[3] += 1
            continue
        h = fp_of(key)
        if h in seen:
            d[4] += 1
            continue
        seen.add(h)
        new_hashes.append(h)
        d[2] += 1
        (new_cfg if kind == "cfg" else new_prox).append(line)
    new_cfg.reverse(); new_prox.reverse()        # تازه‌ترین‌ها اول

    tot = [sum(v[i] for v in delta.values()) for i in range(5)]
    print(f"خلاصه: کانال موفق {stats['ok']}/{len(roles)}، پیام جدید {stats['msgs']}، "
          f"کانفیگ یکتا {len(new_cfg)}، پروکسی یکتا {len(new_prox)} (تکراری {tot[4]}، اسپم {tot[3]})، فایل قفل‌دار {n_files}"
          + (f"، کش بازسازی‌شده {stats['healed']}" if stats["healed"] else "")
          + (f"، خطاها: {sorted(set(errs))}" if errs else ""))
    ok = True
    try:
        for kind, lines in (("configs", new_cfg), ("proxies", new_prox)):
            if lines:
                r = await asyncio.to_thread(api, "/push", {"kind": kind, "lines": lines})
                print(f"{kind}: {r.get('added', 0)} جدید از {r.get('received', 0)}")
    except Exception as e:
        ok = False
        print(f"push failed: {describe(e)}")
        if "403" in str(e):
            try:
                print("تشخیص توکن Worker:", await asyncio.to_thread(api, "/diag", {}))
            except Exception as e2:
                print(f"تشخیص ممکن نشد: {describe(e2)} (Worker رو به نسخه‌ی جدید آپدیت کن)")
    if ok:                                       # فقط بعد از نوشتن موفق، وضعیت و آمار ثبت می‌شه
        state["last"].update(new_last)
        state["seen"] = (state["seen"] + new_hashes)[-SEEN_CAP:]
        cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=STATS_DAYS)).strftime("%Y-%m-%d")
        for src, d in delta.items():
            days = state["stats"].setdefault(src, {})
            cur = days.setdefault(day, [0, 0, 0, 0, 0])
            for i in range(5):
                cur[i] += d[i]
            for old in [k for k in days if k < cutoff]:
                del days[old]
        state["stats"] = {k: v for k, v in state["stats"].items() if k in roles}
        state["roles"] = {k: sorted(v) for k, v in roles.items()}
        state["updated"] = int(time.time())
    try:
        await asyncio.to_thread(api, "/state", {"name": "collect", "data": state})
    except Exception as e:
        print(f"ذخیره‌ی وضعیت شکست خورد: {describe(e)}")
        ok = False
    if not ok:
        sys.exit(1)


asyncio.run(main())

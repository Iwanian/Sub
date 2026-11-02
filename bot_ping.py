# هر ~۶۵ دقیقه، هر حساب تلگرام (TG_SESSION، TG_SESSION_2 ...) یه پیام برای ربات می‌فرسته، منتظر پیام حاوی
# لینک اشتراک (یا کانفیگ) می‌مونه، کانفیگ‌ها رو درمیاره، اسمشون رو عوض می‌کنه و تو Iwana.txt می‌ذاره.
# تنظیمات از Secret ها: BOT_USERNAME, BOT_TEXT (اختیاری: BOT_INTERVAL, NEW_NAME, OUTPUT_FILE, BOT_WAIT).
# زمان‌بندی هر حساب جدا تو KV کلادفلر می‌مونه.
import os, re, sys, json, time, base64, asyncio, random, pathlib, ipaddress, urllib.request, urllib.error
from urllib.parse import quote, urlsplit

from lib import api, describe, env, accounts

ROOT = pathlib.Path(__file__).parent

BOT = env("BOT_USERNAME").lstrip("@")
TEXT = env("BOT_TEXT", "/start")
NAME = env("NEW_NAME", "@I_w_a_n_a")
try:
    INTERVAL = max(15, int(env("BOT_INTERVAL", "65"))) * 60
except ValueError:
    INTERVAL = 65 * 60
try:
    WAIT = min(240, max(15, int(env("BOT_WAIT", "60"))))      # حداکثر چند ثانیه منتظر پیام‌های ربات بمونه
except ValueError:
    WAIT = 60
_of = env("OUTPUT_FILE", "Iwana.txt")
if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}\.txt", _of):
    sys.exit("output_file نامعتبره (فقط اسم ساده با پسوند .txt)")
OUTFILE = ROOT / _of
FORCE = env("FORCE") == "true"      # اجرای دستی با تیک force: بدون چک زمان
CAP = 500

CFG_RE = re.compile(r'(?:vless|vmess|trojan|ss|hysteria2|hy2|tuic)://[^\s<>"\'`]+', re.I)
URL_RE = re.compile(r'https?://[^\s<>"\'`]+', re.I)
SKIP_HOSTS = ("t.me", "telegram.me", "telegram.org", "telegra.ph")
MAX_SUB_URLS = 5
MAX_SUB_BYTES = 2_000_000


def sub_urls(text):
    """لینک‌های اشتراک احتمالی از متن/دکمه‌ی جواب ربات (لینک‌های تلگرام و IP خصوصی رد می‌شن)."""
    out = []
    for u in URL_RE.findall(text):
        u = u.rstrip(".,;)]}»،")
        host = (urlsplit(u).hostname or "").lower()
        if not host or any(host == s or host.endswith("." + s) for s in SKIP_HOSTS):
            continue
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                continue
        except ValueError:
            pass
        out.append(u)
    return list(dict.fromkeys(out))


def fetch_sub(url):
    req = urllib.request.Request(url, headers={"User-Agent": "v2rayNG/1.8.5", "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read(MAX_SUB_BYTES + 1)
    if len(raw) > MAX_SUB_BYTES:
        raise ValueError("too_large")
    return raw.decode("utf-8", errors="ignore")


def links_from_body(body):
    """اشتراک یا متن ساده‌ست یا base64 (استاندارد/urlsafe)."""
    found = CFG_RE.findall(body)
    if found:
        return found
    compact = re.sub(r"\s+", "", body)
    for fn in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            dec = fn(compact + "=" * (-len(compact) % 4)).decode("utf-8", errors="ignore")
        except Exception:
            continue
        found = CFG_RE.findall(dec)
        if found:
            return found
    return []


def is_junk(link):
    """ورودی‌های اطلاعاتی پنل‌ها (مثل «حجم باقی‌مانده») که سرور واقعی ندارن."""
    try:
        if link.lower().startswith("vmess://"):
            body = link[8:].split("#")[0]
            host = json.loads(base64.b64decode(body + "=" * (-len(body) % 4)))["add"]
        else:
            host = urlsplit(link).hostname or ""
    except Exception:
        return False
    return str(host).lower() in ("0.0.0.0", "127.0.0.1", "localhost", "::1")


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


def due(acc):
    now = time.time()
    return (now - acc.get("last_ok", 0) >= INTERVAL - 90) and (now - acc.get("last_try", 0) >= 900)


async def run_account(idx, session, delay):
    """یک حساب: پیام می‌فرسته و تا کانفیگ واقعی نیومده صبر می‌کنه. خطا فقط همین حساب رو متوقف می‌کنه."""
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.tl.types import MessageEntityTextUrl

    await asyncio.sleep(delay)             # فاصله بین حساب‌ها تا همه با هم به ربات فشار نیارن
    found, seen, fetched = [], {}, {}      # fetched: لینک اشتراک -> نتیجه (آدرس هیچ‌جا چاپ نمی‌شه)
    try:
        async with TelegramClient(StringSession(session), int(env("TG_API_ID")), env("TG_API_HASH")) as c:
            peer = await c.get_input_entity(BOT)      # هر بار پیدا می‌شه؛ چیزی از ربات ذخیره نمی‌شه
            sent = await c.send_message(peer, TEXT)
            # پیام اول ممکنه فقط «صبر کن» باشه و لینک تو پیام دوم بیاد؛ پس تا کانفیگ واقعی نیومده صبر می‌کنیم
            for _ in range(max(1, WAIT // 3)):
                await asyncio.sleep(3)
                urls = []
                async for m in c.iter_messages(peer, min_id=sent.id, limit=10):
                    if m.out:
                        continue
                    t = m.raw_text or ""
                    for e in (m.entities or []):
                        if isinstance(e, MessageEntityTextUrl):
                            t += "\n" + e.url
                    try:
                        for row in (m.buttons or []):
                            for b in row:
                                if getattr(b, "url", None):
                                    t += "\n" + b.url
                    except Exception:
                        pass
                    found += CFG_RE.findall(t)
                    urls += sub_urls(t)
                    seen[m.id] = {"btn": m.reply_markup is not None, "media": m.media is not None,
                                  "schemes": set(s.lower() for s in re.findall(r"([A-Za-z][A-Za-z0-9+.-]{1,12})://", t))}
                for u in list(dict.fromkeys(urls))[:MAX_SUB_URLS]:
                    if u in fetched:
                        continue
                    try:
                        got = links_from_body(await asyncio.to_thread(fetch_sub, u))
                        fetched[u] = f"{len(got)} کانفیگ"
                        found += got
                    except urllib.error.HTTPError as e:
                        fetched[u] = f"HTTP {e.code}"
                    except Exception as e:
                        fetched[u] = f"خطا ({type(e).__name__})"
                if found:
                    break
    except Exception as e:
        return {"idx": idx, "error": type(e).__name__}
    return {"idx": idx, "found": list(dict.fromkeys(found)), "seen": seen, "fetched": list(fetched.values())}


async def run_all(todo):
    jobs = [run_account(i, s, k * random.uniform(5, 15)) for k, (i, s) in enumerate(todo)]
    return await asyncio.gather(*jobs)


def main():
    if not BOT:
        print("Secret به اسم BOT_USERNAME تنظیم نشده")
        return
    accs = accounts()
    if not accs:
        print("هیچ Secret ای به اسم TG_SESSION تنظیم نشده")
        return
    try:
        state = api("/state", {"name": "bot"}, 60).get("data") or {}
    except Exception as e:
        print(f"خوندن وضعیت از Worker شکست خورد: {describe(e)}")
        return                                    # بدون وضعیت چیزی نمی‌فرستیم
    if "acc" not in state:                        # مهاجرت از نسخه‌ی تک‌حسابه
        state = {"acc": {"1": {k: state[k] for k in ("last_ok", "last_try") if k in state}}}
    state["acc"] = {k: v for k, v in state["acc"].items() if k in {str(i) for i, _ in accs}}

    todo = [(i, s) for i, s in accs if FORCE or due(state["acc"].get(str(i), {}))]
    if not todo:
        print(f"هنوز وقت هیچ‌کدوم از {len(accs)} حساب نشده")
        return
    now = time.time()
    for i, _ in todo:
        state["acc"].setdefault(str(i), {})["last_try"] = now
    try:
        api("/state", {"name": "bot", "data": state}, 60)
    except Exception as e:
        print(f"ذخیره‌ی وضعیت شکست خورد: {describe(e)}")
        return
    print(f"{len(todo)} حساب از {len(accs)} حساب فعال")

    all_found, got_any = [], set()
    for res in asyncio.run(run_all(todo)):
        n = res["idx"]
        if "error" in res:
            print(f"حساب {n}: خطا ({res['error']}) — سشن یا دسترسی رو چک کن")
            continue
        seen = res["seen"]
        # گزارش بدون افشای محتوا: فقط شمارش و نوع لینک‌ها
        if not seen:
            print(f"حساب {n}: ربات تا {WAIT} ثانیه جواب نداد (ربات رو با این حساب Start کردی؟)")
        else:
            schemes = sorted(set().union(*[v["schemes"] for v in seen.values()]))
            print(f"حساب {n}: {len(seen)} پیام، {sum(v['btn'] for v in seen.values())} با دکمه، "
                  f"{sum(v['media'] for v in seen.values())} با فایل/عکس، نوع لینک‌ها: {schemes or 'هیچ'}")
        for k, r in enumerate(res["fetched"], 1):
            print(f"حساب {n}، اشتراک {k}: {r}")
        good = [x for x in res["found"] if not is_junk(x)]
        if len(good) != len(res["found"]):
            print(f"حساب {n}: {len(res['found']) - len(good)} مورد اطلاعاتی (بدون سرور واقعی) حذف شد")
        if good:
            got_any.add(str(n))
            all_found += good

    new = [r for r in (rename(x) for x in dict.fromkeys(all_found)) if r]
    if not new:
        print("کانفیگی از هیچ حسابی پیدا نشد")
        return
    old = OUTFILE.read_text(encoding="utf-8").splitlines() if OUTFILE.exists() else []
    merged = []
    for l in new + old:                           # تازه‌ترین‌ها بالا
        if l and l not in merged:
            merged.append(l)
    OUTFILE.write_text("\n".join(merged[:CAP]) + "\n", encoding="utf-8")
    now = time.time()
    for k in got_any:                             # حساب‌هایی که چیزی نگرفتن، بعد از ۱۵ دقیقه دوباره امتحان می‌شن
        state["acc"][k]["last_ok"] = now
    try:
        api("/state", {"name": "bot", "data": state}, 60)
    except Exception as e:
        print(f"ذخیره‌ی وضعیت شکست خورد: {describe(e)}")
    print(f"{len(new)} کانفیگ ذخیره شد (از {len(got_any)} حساب)")


main()

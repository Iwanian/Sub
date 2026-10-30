# هر ~۶۵ دقیقه یه پیام برای ربات می‌فرسته، کانفیگ جوابش رو اسم‌گذاری می‌کنه و تو Iwana.txt می‌ذاره
import os, re, sys, json, time, base64, asyncio, pathlib
from urllib.parse import quote

ROOT = pathlib.Path(__file__).parent
S = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
STATE = ROOT / "bot_state.json"

# یوزرنیم ربات از Secret به اسم BOT_USERNAME خونده می‌شه (تو ریپو نوشته نمی‌شه)
BOT = (os.environ.get("BOT_USERNAME") or S.get("bot_username") or "").lstrip("@").strip()
TEXT = S.get("bot_text") or "/start"
NAME = S.get("new_name") or "@I_w_a_n_a"
INTERVAL = int(S.get("interval_minutes") or 65) * 60
_of = S.get("output_file") or "Iwana.txt"
if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}\.txt", _of):
    sys.exit("output_file نامعتبره (فقط اسم ساده با پسوند .txt)")
OUTFILE = ROOT / _of
CAP = 500

CFG_RE = re.compile(r'(?:vless|vmess|trojan|ss|hysteria2|hy2|tuic)://[^\s<>"\'`]+', re.I)


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


async def run(state):
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.tl.types import MessageEntityTextUrl

    async with TelegramClient(StringSession(os.environ["TG_SESSION"]),
                              int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"]) as c:
        peer = await c.get_input_entity(BOT)      # هر بار پیدا می‌شه؛ چیزی درباره‌ی ربات تو ریپو ذخیره نمی‌شه

        sent = await c.send_message(peer, TEXT)
        found = []
        for _ in range(8):                      # تا ~۴۰ ثانیه منتظر جواب می‌مونه
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
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    state.pop("bot_peer", None)
    if not due(state):
        print("هنوز وقتش نشده")
        return
    state["last_try"] = time.time()
    try:
        found = asyncio.run(run(state))
    finally:
        STATE.write_text(json.dumps(state))

    new = [r for r in (rename(x) for x in found) if r]
    if not new:
        print("کانفیگی تو جواب ربات پیدا نشد")
        return
    old = OUTFILE.read_text(encoding="utf-8").splitlines() if OUTFILE.exists() else []
    merged = []
    for l in new + old:                         # تازه‌ترین‌ها بالا
        if l and l not in merged:
            merged.append(l)
    OUTFILE.write_text("\n".join(merged[:CAP]) + "\n", encoding="utf-8")
    state["last_ok"] = time.time()
    STATE.write_text(json.dumps(state))
    print(f"{len(new)} کانفیگ ذخیره شد")


main()

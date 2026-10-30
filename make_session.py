# یک بار روی گوشی/کامپیوتر خودت اجرا کن (pip install telethon).
# شماره، کد تلگرام و (اگه داری) رمز دومرحله‌ای رو می‌پرسه و TG_SESSION رو چاپ می‌کنه.
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = int(input("API ID: ").strip())
api_hash = input("API HASH: ").strip()
with TelegramClient(StringSession(), api_id, api_hash) as client:
    print("\nاین رشته رو تو GitHub Secrets با اسم TG_SESSION بذار و به کسی نده:\n")
    print(client.session.save())

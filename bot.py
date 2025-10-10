import asyncio
import glob
import importlib
import sys
import time
from datetime import date, datetime, timedelta
import html
from pathlib import Path

import pytz
from aiohttp import web
from PIL import Image
from pyrogram import Client, __version__, filters, idle
from pyrogram.errors import FloodWait
from pyrogram.raw.all import layer

from database.ia_filterdb import Media, Media2
from database.users_chats_db import db
from dreamxbotz.Bot import dreamxbotz
from dreamxbotz.Bot.clients import initialize_clients
from dreamxbotz.util.keepalive import ping_server
from info import *
from plugins import check_expired_premium, keep_alive, web_server
from Script import script
from utils import temp

Image.MAX_IMAGE_PIXELS = 500_000_000

import logging
import logging.config

logging.config.fileConfig("logging.conf")
logging.getLogger().setLevel(logging.INFO)
logging.getLogger("pyrogram").setLevel(logging.ERROR)
logging.getLogger("imdbpy").setLevel(logging.ERROR)
logging.getLogger("aiohttp").setLevel(logging.ERROR)
logging.getLogger("aiohttp.web").setLevel(logging.ERROR)
logging.getLogger("pymongo").setLevel(logging.WARNING)

botStartTime = time.time()
ppath = "plugins/*.py"
files = glob.glob(ppath)

# ----------------------------
# AUTO DELETE USER MESSAGES + LOGGING (PM + SELECTED GROUPS)
# ----------------------------

DELETE_DELAY = AUTO_DELETE_DELAY_HOURS * 3600  # hours → seconds

@dreamxbotz.on_message(
    (filters.private | filters.chat(AUTO_DELETE_GROUPS)) & ~filters.service
)
async def auto_delete_message(client, message):
    """
    Automatically delete user messages after a configurable delay.
    Logs deleted PM messages (and optionally group messages) in LOG_CHANNEL.
    Skips admins, trusted users, and bot messages.
    """
    try:
        # Skip bot messages
        if not message.from_user or message.from_user.is_bot:
            return

        user = message.from_user
        chat = message.chat
        user_name = f"@{user.username}" if user.username else user.first_name

        # Skip trusted users
        if user.id in TRUSTED_USERS:
            logging.info(f"🛡️ Skipping trusted user {user_name} ({user.id})")
            return

        # Skip group admins
        if chat.type in ["supergroup", "group"]:
            try:
                member = await client.get_chat_member(chat.id, user.id)
                if member.status in ["administrator", "creator"]:
                    logging.info(
                        f"👑 Skipping admin {user_name} ({user.id}) in {chat.title}"
                    )
                    return
            except Exception as e:
                logging.debug(f"⚠️ Could not check admin status for {user_name}: {e}")

        # Determine message type
        if message.text:
            msg_type = "text"
        elif message.photo:
            msg_type = "photo"
        elif message.video:
            msg_type = "video"
        elif message.document:
            msg_type = "document"
        elif message.sticker:
            msg_type = "sticker"
        elif message.voice:
            msg_type = "voice"
        elif message.audio:
            msg_type = "audio"
        elif message.animation:
            msg_type = "animation"
        else:
            msg_type = "unknown"

        # Generate preview text
        caption = getattr(message, "caption", "") or ""
        if message.document:
            file_name = message.document.file_name
        elif message.video:
            file_name = getattr(message.video, "file_name", "Video")
        elif message.audio:
            file_name = getattr(message.audio, "file_name", "Audio")
        elif message.animation:
            file_name = getattr(message.animation, "file_name", "Animation")
        elif message.photo:
            file_name = "Photo"
        elif message.sticker:
            file_name = "Sticker"
        elif message.voice:
            file_name = "Voice"
        elif message.text:
            file_name = "Text"
        else:
            file_name = "Unknown"

        preview_text = f"{file_name} | Caption: {caption[:100]}" if caption else file_name

        logging.info(
            f"🕓 Scheduled deletion for {chat.type} message from {user_name} ({user.id}) | Type: {msg_type} | Delay: {AUTO_DELETE_DELAY_HOURS}h"
        )

        # Wait with periodic checks for PMs (early deletion if blocked)
        if chat.type == "private":
            total_wait = 0
            interval = 600  # 10 minutes
            while total_wait < DELETE_DELAY:
                try:
                    await client.send_chat_action(chat.id, "typing")
                except Exception:
                    try:
                        await message.delete()
                        await send_log_message(
                            client,
                            user,
                            msg_type,
                            "early-deleted (user blocked)",
                            preview_text,
                            chat_type="private",
                        )
                        logging.info(f"🧹 Early deleted message from {user_name} ({user.id})")
                    except Exception as ex:
                        logging.warning(f"⚠️ Could not early-delete message from {user_name}: {ex}")
                    return
                await asyncio.sleep(interval)
                total_wait += interval
        else:
            # For groups, simply wait the full delay
            await asyncio.sleep(DELETE_DELAY)

        # Delete message after delay
        try:
            await message.delete()
            logging.info(f"✅ Deleted message from {user_name} ({user.id}) after {AUTO_DELETE_DELAY_HOURS}h")

            # Logging
            if chat.type == "private" or LOG_GROUP_MESSAGES:
                chat_type_str = f"private" if chat.type == "private" else f"group ({chat.title})"
                await send_log_message(
                    client,
                    user,
                    msg_type,
                    "deleted after delay",
                    preview_text,
                    chat_type=chat_type_str,
                )

        except Exception as e:
            logging.warning(f"⚠️ Could not delete message from {user_name} ({user.id}): {e}")

    except Exception as e:
        logging.error(f"❌ Error in auto-delete task: {e}")


async def send_log_message(client, user, msg_type, status, preview_text, chat_type="private"):
    """
    Sends a detailed log entry to LOG_CHANNEL.
    Includes file name and caption for media messages.
    """
    if not LOG_CHANNEL:
        return

    try:
        first_name = html.escape(user.first_name or "Unknown")
        safe_preview = html.escape(preview_text)
        safe_status = html.escape(status)
        safe_type = html.escape(msg_type)
        safe_chat = html.escape(chat_type)

        user_link = f"<a href='tg://user?id={user.id}'>{first_name}</a>"
        time_now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

        log_text = (
            f"🧹 <b>Message Deleted</b>\n\n"
            f"👤 User: {user_link} (<code>{user.id}</code>)\n"
            f"💬 Type: <b>{safe_type}</b>\n"
            f"⏱ Status: <i>{safe_status}</i>\n"
            f"🕒 Time: {time_now}\n"
            f"📌 Chat: {safe_chat}\n\n"
            f"📄 Preview/File: <code>{safe_preview}</code>"
        )

        await client.send_message(
            LOG_CHANNEL, log_text, disable_web_page_preview=True, parse_mode="HTML"
        )
        logging.info(f"📨 Logged deleted message from {user.id} to LOG_CHANNEL")

    except Exception as e:
        logging.warning(f"⚠️ Could not send log message for user {user.id}: {e}")

async def dreamxbotz_start():
    print('\n\nInitalizing DreamxBotz')
    await dreamxbotz.start()
    bot_info = await dreamxbotz.get_me()
    dreamxbotz.username = bot_info.username
    await initialize_clients()
    for name in files:
        with open(name) as a:
            patt = Path(a.name)
            plugin_name = patt.stem.replace(".py", "")
            plugins_dir = Path(f"plugins/{plugin_name}.py")
            import_path = "plugins.{}".format(plugin_name)
            spec = importlib.util.spec_from_file_location(import_path, plugins_dir)
            load = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(load)
            sys.modules["plugins." + plugin_name] = load
            print("DreamxBotz Imported => " + plugin_name)
    if ON_HEROKU:
        asyncio.create_task(ping_server()) 
    b_users, b_chats = await db.get_banned()
    temp.BANNED_USERS = b_users
    temp.BANNED_CHATS = b_chats
    await Media.ensure_indexes()
    if MULTIPLE_DB:
        await Media2.ensure_indexes()
        print("Multiple Database Mode On. Now Files Will Be Save In Second DB If First DB Is Full")
    else:
        print("Single DB Mode On ! Files Will Be Save In First Database")
    me = await dreamxbotz.get_me()
    temp.ME = me.id
    temp.U_NAME = me.username
    temp.B_NAME = me.first_name
    temp.B_LINK = me.mention
    dreamxbotz.username = '@' + me.username
    dreamxbotz.loop.create_task(check_expired_premium(dreamxbotz))
    logging.info(f"{me.first_name} with Pyrogram v{__version__} (Layer {layer}) started on {me.username}.")
    logging.info(LOG_STR)
    logging.info(script.LOGO)
    tz = pytz.timezone('Asia/Kolkata')
    today = date.today()
    now = datetime.now(tz)
    time = now.strftime("%H:%M:%S %p")
    await dreamxbotz.send_message(chat_id=LOG_CHANNEL, text=script.RESTART_TXT.format(temp.B_LINK, today, time))
    app = web.AppRunner(await web_server())
    await app.setup()
    bind_address = "0.0.0.0"
    await web.TCPSite(app, bind_address, PORT).start()
    dreamxbotz.loop.create_task(keep_alive())
    await idle()
    
if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    while True:
        try:
            loop.run_until_complete(dreamxbotz_start())
            break  
        except FloodWait as e:
            print(f"FloodWait! Sleeping for {e.value} seconds.")
            time.sleep(e.value) 
        except KeyboardInterrupt:
            logging.info('Service Stopped Bye 👋')
            break

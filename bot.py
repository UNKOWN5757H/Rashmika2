import asyncio
import glob
import importlib
import sys
import time
from datetime import date, datetime, timedelta
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

DELETE_DELAY = AUTO_DELETE_DELAY_HOURS * 60 * 60  # hours → seconds
LOG_GROUP_MESSAGES = True  # Set False if you don't want group messages logged


@dreamxbotz.on_message(
    (filters.private | filters.chat(AUTO_DELETE_GROUPS))
    & ~filters.service
    & ~filters.bot
)
async def auto_delete_message(client, message):
    """
    Automatically delete user messages after configurable delay.
    Logs deleted PM messages (and optionally group messages) in LOG_CHANNEL.
    Includes file name and caption for media.
    Skips admins, trusted users, and bot messages.
    """
    try:
        # Skip bot messages
        if message.from_user and message.from_user.is_self:
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
                    logging.info(f"👑 Skipping admin {user_name} ({user.id}) in {chat.title}")
                    return
            except Exception as e:
                logging.warning(f"⚠️ Failed to check admin status for {user_name}: {e}")

        # Determine message type
        msg_type = (
            "text" if message.text
            else "photo" if message.photo
            else "video" if message.video
            else "document" if message.document
            else "sticker" if message.sticker
            else "voice" if message.voice
            else "audio" if message.audio
            else "animation" if message.animation
            else "unknown"
        )

        # File name or caption preview
        if message.document:
            file_name = message.document.file_name
            caption = message.caption or ""
            preview_text = f"{file_name} | Caption: {caption[:100]}"
        elif message.video:
            file_name = getattr(message.video, "file_name", "Video")
            caption = message.caption or ""
            preview_text = f"{file_name} | Caption: {caption[:100]}"
        elif message.audio:
            file_name = getattr(message.audio, "file_name", "Audio")
            caption = message.caption or ""
            preview_text = f"{file_name} | Caption: {caption[:100]}"
        elif message.animation:
            file_name = getattr(message.animation, "file_name", "Animation")
            caption = message.caption or ""
            preview_text = f"{file_name} | Caption: {caption[:100]}"
        elif message.photo:
            caption = message.caption or ""
            preview_text = f"Photo | Caption: {caption[:100]}"
        elif message.sticker:
            preview_text = "Sticker"
        elif message.voice:
            preview_text = "Voice"
        elif message.text:
            preview_text = message.text[:100]
        else:
            preview_text = "Unknown content"

        logging.info(
            f"🕓 Scheduled deletion for {chat.type} message from {user_name} ({user.id}) | Type: {msg_type} | Delay: {AUTO_DELETE_DELAY_HOURS}h"
        )

        # Wait with periodic checks (10 min)
        total_wait = 0
        interval = 600
        while total_wait < DELETE_DELAY:
            await asyncio.sleep(interval)
            total_wait += interval

            # Early deletion in PMs
            if chat.type == "private":
                try:
                    await client.send_chat_action(chat.id, "typing")
                except Exception:
                    try:
                        await message.delete()
                        await send_log_message(client, user, msg_type, "early-deleted (user blocked)", preview_text, chat_type="private")
                        logging.info(f"🧹 Early deleted message from {user_name} ({user.id})")
                    except Exception as ex:
                        logging.warning(f"⚠️ Could not early-delete message from {user_name}: {ex}")
                    return

        # Delete after delay
        try:
            await message.delete()
            logging.info(f"✅ Deleted message from {user_name} ({user.id}) after {AUTO_DELETE_DELAY_HOURS}h")

            # Logging
            if chat.type == "private" or LOG_GROUP_MESSAGES:
                chat_type = "private" if chat.type == "private" else f"group ({chat.title})"
                await send_log_message(client, user, msg_type, "deleted after delay", preview_text, chat_type)

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
        user_link = f"<a href='tg://user?id={user.id}'>{user.first_name}</a>"
        time_now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

        log_text = (
            f"🧹 <b>Message Deleted</b>\n\n"
            f"👤 User: {user_link} (<code>{user.id}</code>)\n"
            f"💬 Type: <b>{msg_type}</b>\n"
            f"⏱ Status: <i>{status}</i>\n"
            f"🕒 Time: {time_now}\n"
            f"📌 Chat: {chat_type}\n\n"
            f"📄 Preview/File: <code>{preview_text}</code>"
        )

        await client.send_message(LOG_CHANNEL, log_text, disable_web_page_preview=True)
        logging.info(f"📨 Logged deleted message from {user.id} to LOG_CHANNEL")

    except Exception as e:
        logging.warning(f"⚠️ Could not send log message: {e}")


# ----------------------------
# MAIN BOT START FUNCTION
# ----------------------------


async def dreamxbotz_start():
    print("\n\nInitalizing DreamxBotz")
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
        print(
            "Multiple Database Mode On. Now Files Will Be Save In Second DB If First DB Is Full"
        )
    else:
        print("Single DB Mode On ! Files Will Be Save In First Database")

    me = await dreamxbotz.get_me()
    temp.ME = me.id
    temp.U_NAME = me.username
    temp.B_NAME = me.first_name
    temp.B_LINK = me.mention
    dreamxbotz.username = "@" + me.username

    dreamxbotz.loop.create_task(check_expired_premium(dreamxbotz))

    logging.info(
        f"{me.first_name} with Pyrogram v{__version__} (Layer {layer}) started on {me.username}."
    )
    logging.info(LOG_STR)
    logging.info(script.LOGO)

    tz = pytz.timezone("Asia/Kolkata")
    today = date.today()
    now = datetime.now(tz)
    current_time = now.strftime("%H:%M:%S %p")

    await dreamxbotz.send_message(
        chat_id=LOG_CHANNEL,
        text=script.RESTART_TXT.format(temp.B_LINK, today, current_time),
    )

    app = web.AppRunner(await web_server())
    await app.setup()
    bind_address = "0.0.0.0"
    await web.TCPSite(app, bind_address, PORT).start()

    dreamxbotz.loop.create_task(keep_alive())
    await idle()


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    while True:
        try:
            loop.run_until_complete(dreamxbotz_start())
            break
        except FloodWait as e:
            print(f"FloodWait! Sleeping for {e.value} seconds.")
            time.sleep(e.value)
        except KeyboardInterrupt:
            logging.info("Service Stopped Bye 👋")
            break

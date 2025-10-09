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
# AUTO DELETE PM TEXTS (4 HOURS)
# ----------------------------

DELETE_DELAY = 4 * 60 * 60  # 4 hours in seconds


@dreamxbotz.on_message(filters.private & ~filters.service & ~filters.bot)
async def auto_delete_pm_text(client, message):
    """
    Automatically delete private messages after 4 hours.
    """
    try:
        # Skip messages sent by the bot itself
        if message.from_user and message.from_user.is_self:
            return

        await asyncio.sleep(DELETE_DELAY)

        # Try deleting the message safely
        try:
            await message.delete()
            logging.info(
                f"Deleted PM message from {message.from_user.id} after 4 hours."
            )
        except Exception as e:
            logging.warning(
                f"Failed to delete message from {message.from_user.id}: {e}"
            )

    except Exception as e:
        logging.error(f"Error in auto-delete task: {e}")


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

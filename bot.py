#!/usr/bin/env python3
"""
DreamxBotz Main Bot File
Fully integrated auto-delete, logging, active-only PM dashboard with live stats and colored history,
plugins, web server, keepalive, and premium checks.
"""

import asyncio
import glob
import html
import importlib
import os
import sys
import time
from datetime import date, datetime
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
plugin_files = glob.glob("plugins/*.py")

DELETE_DELAY = AUTO_DELETE_DELAY_HOURS * 3600  # hours → seconds
DEBUG_MODE = True  # PM dashboard

# ----------------------------
# ACTIVE PM COUNTDOWNS & HISTORY
# ----------------------------
ACTIVE_PM_COUNTDOWNS = {}
TOTAL_DELETED = 0
TOTAL_EARLY_DELETED = 0
HISTORY_LOG = []


# ----------------------------
# ANSI COLORS FOR DASHBOARD
# ----------------------------
class bcolors:
    HEADER = "\033[95m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"


# ----------------------------
# AUTO DELETE USER MESSAGES + LOGGING
# ----------------------------
@dreamxbotz.on_message(
    (filters.private | filters.chat(AUTO_DELETE_GROUPS)) & ~filters.service
)
async def auto_delete_message(client, message):
    global TOTAL_DELETED, TOTAL_EARLY_DELETED, ACTIVE_PM_COUNTDOWNS, HISTORY_LOG

    if not message.from_user or message.from_user.is_bot:
        return

    user = message.from_user
    chat = message.chat
    user_name = f"@{user.username}" if user.username else user.first_name

    if user.id in TRUSTED_USERS:
        return

    if chat.type in ["supergroup", "group"]:
        try:
            member = await client.get_chat_member(chat.id, user.id)
            if member.status in ["administrator", "creator"]:
                return
        except Exception:
            pass

    msg_type = (
        "text"
        if message.text
        else (
            "photo"
            if message.photo
            else (
                "video"
                if message.video
                else (
                    "document"
                    if message.document
                    else (
                        "sticker"
                        if message.sticker
                        else (
                            "voice"
                            if message.voice
                            else (
                                "audio"
                                if message.audio
                                else "animation" if message.animation else "unknown"
                            )
                        )
                    )
                )
            )
        )
    )

    caption = getattr(message, "caption", "") or ""
    file_name = (
        getattr(message.document, "file_name", None)
        or getattr(message.video, "file_name", None)
        or getattr(message.audio, "file_name", None)
        or getattr(message.animation, "file_name", None)
        or "Photo"
        if message.photo
        else (
            "Sticker"
            if message.sticker
            else "Voice" if message.voice else "Text" if message.text else "Unknown"
        )
    )
    preview_text = f"{file_name} | Caption: {caption[:100]}" if caption else file_name

    total_wait = DELETE_DELAY
    interval = 1 if DEBUG_MODE else 600  # 1s for dashboard, 10min normal

    if DEBUG_MODE and chat.type == "private":
        ACTIVE_PM_COUNTDOWNS[message.message_id] = {
            "user": user_name,
            "type": msg_type,
            "remaining": total_wait,
        }

    try:
        while total_wait > 0:
            if DEBUG_MODE and chat.type == "private":
                ACTIVE_PM_COUNTDOWNS[message.message_id]["remaining"] = total_wait

            # Early deletion for PMs if user blocked
            if chat.type == "private":
                try:
                    await client.send_chat_action(chat.id, "typing")
                except Exception:
                    try:
                        await message.delete()
                        TOTAL_EARLY_DELETED += 1

                        if DEBUG_MODE:
                            ACTIVE_PM_COUNTDOWNS.pop(message.message_id, None)
                            HISTORY_LOG.append(
                                {
                                    "user": user_name,
                                    "type": msg_type,
                                    "time": datetime.now().strftime("%H:%M:%S"),
                                    "status": "Early-deleted",
                                }
                            )

                        await send_log_message(
                            client,
                            user,
                            msg_type,
                            "early-deleted (user blocked)",
                            preview_text,
                            chat_type="private",
                        )
                    except Exception:
                        pass
                    return

            await asyncio.sleep(interval)
            total_wait -= interval

        # Delete after full countdown
        await message.delete()
        TOTAL_DELETED += 1

        if DEBUG_MODE and chat.type == "private":
            ACTIVE_PM_COUNTDOWNS.pop(message.message_id, None)
            HISTORY_LOG.append(
                {
                    "user": user_name,
                    "type": msg_type,
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "status": "Deleted",
                }
            )

        if chat.type == "private" or LOG_GROUP_MESSAGES:
            chat_type_str = (
                "private" if chat.type == "private" else f"group ({chat.title})"
            )
            await send_log_message(
                client,
                user,
                msg_type,
                "deleted after delay",
                preview_text,
                chat_type=chat_type_str,
            )

    except Exception:
        if DEBUG_MODE:
            ACTIVE_PM_COUNTDOWNS.pop(message.message_id, None)


# ----------------------------
# SEND LOG MESSAGE
# ----------------------------
async def send_log_message(
    client, user, msg_type, status, preview_text, chat_type="private"
):
    if not LOG_CHANNEL or user.is_bot:
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
    except Exception:
        pass


# ----------------------------
# COLORED SCROLLABLE PM DASHBOARD
# ----------------------------
async def pm_countdown_history_dashboard():
    global TOTAL_DELETED, TOTAL_EARLY_DELETED, ACTIVE_PM_COUNTDOWNS, HISTORY_LOG

    while True:
        if DEBUG_MODE:
            now_time = datetime.now().strftime("%H:%M:%S")
            print(f"{bcolors.BOLD}[DreamxBotz PM Dashboard]{bcolors.ENDC}")
            print(
                f"Stats -> Active PMs: {bcolors.OKGREEN}{len(ACTIVE_PM_COUNTDOWNS)}{bcolors.ENDC} | "
                f"Deleted: {bcolors.OKGREEN}{TOTAL_DELETED}{bcolors.ENDC} | "
                f"Early-deleted/Blocked: {bcolors.FAIL}{TOTAL_EARLY_DELETED}{bcolors.ENDC}"
            )
            print("-" * 90)
            print(
                f"{'Time':<10} {'User':<25} {'Type':<12} {'Remaining(s)':>12} {'Status':<20}"
            )
            print("-" * 90)

            to_remove = []
            for msg_id, info in ACTIVE_PM_COUNTDOWNS.items():
                remaining = int(info["remaining"])
                if remaining <= 0:
                    to_remove.append(msg_id)
                    continue

                color = (
                    bcolors.OKGREEN
                    if remaining > 60
                    else bcolors.WARNING if remaining > 30 else bcolors.FAIL
                )
                print(
                    f"{now_time:<10} {info['user']:<25} {info['type']:<12} {color}{remaining:>12}{bcolors.ENDC} {'Active':<20}"
                )

            for msg_id in to_remove:
                finished = ACTIVE_PM_COUNTDOWNS.pop(msg_id)
                finished["time"] = now_time
                finished["status"] = "Deleted"
                HISTORY_LOG.append(finished)

            if HISTORY_LOG:
                print("\nRecent History (last 10 messages):")
                print("-" * 90)
                for entry in HISTORY_LOG[-10:]:
                    status_color = (
                        bcolors.OKGREEN
                        if entry["status"] == "Deleted"
                        else bcolors.FAIL
                    )
                    print(
                        f"{entry['time']:<10} {entry['user']:<25} {entry['type']:<12} {'-'*12} {status_color}{entry['status']:<20}{bcolors.ENDC}"
                    )

            print("-" * 90)
        await asyncio.sleep(1)


# ----------------------------
# BOT STARTUP
# ----------------------------
async def dreamxbotz_start():
    print("\n\nInitializing DreamxBotz…")
    await dreamxbotz.start()
    bot_info = await dreamxbotz.get_me()
    dreamxbotz.username = bot_info.username

    await initialize_clients()

    for plugin_path in plugin_files:
        plugin_name = Path(plugin_path).stem
        spec = importlib.util.spec_from_file_location(
            f"plugins.{plugin_name}", plugin_path
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[f"plugins.{plugin_name}"] = module
        print(f"DreamxBotz Imported => {plugin_name}")

    if ON_HEROKU:
        asyncio.create_task(ping_server())

    b_users, b_chats = await db.get_banned()
    temp.BANNED_USERS = b_users
    temp.BANNED_CHATS = b_chats

    await Media.ensure_indexes()
    if MULTIPLE_DB:
        await Media2.ensure_indexes()
        print("Multiple Database Mode On")
    else:
        print("Single DB Mode On")

    me = await dreamxbotz.get_me()
    temp.ME = me.id
    temp.U_NAME = me.username
    temp.B_NAME = me.first_name
    temp.B_LINK = me.mention
    dreamxbotz.username = f"@{me.username}"

    dreamxbotz.loop.create_task(check_expired_premium(dreamxbotz))
    if DEBUG_MODE:
        dreamxbotz.loop.create_task(pm_countdown_history_dashboard())

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
        LOG_CHANNEL, script.RESTART_TXT.format(temp.B_LINK, today, current_time)
    )

    app = web.AppRunner(await web_server())
    await app.setup()
    await web.TCPSite(app, "0.0.0.0", PORT).start()
    dreamxbotz.loop.create_task(keep_alive())
    await idle()


# ----------------------------
# MAIN ENTRY
# ----------------------------
if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    while True:
        try:
            loop.run_until_complete(dreamxbotz_start())
            break
        except FloodWait as e:
            print(f"FloodWait! Sleeping for {e.value} seconds...")
            time.sleep(e.value)
        except KeyboardInterrupt:
            logging.info("Service Stopped. Bye 👋")
            break
        except Exception as e:
            logging.error(f"❌ Unexpected error in main loop: {e}")
            time.sleep(5)

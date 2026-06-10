import asyncio
import logging
import os
import json
import sqlite3
from datetime import datetime, timedelta
from pyrogram import Client, filters
from pyrogram.types import Message
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler,
    filters as tg_filters, ContextTypes
)
from telegram.constants import ParseMode

# ============================================================
#                    CONFIGURATION
# ============================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")
API_ID = int(os.environ.get("API_ID", 123456))
API_HASH = os.environ.get("API_HASH", "your_api_hash")
SESSION_STRING = os.environ.get("SESSION_STRING", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 123456789))

BOT_B_USERNAME = "BookTherepybot"
STREAM_BOT_USERNAME = "aishwariyaupdatesbot"  # Stream bot username (forward ke liye)
STREAM_BOT_ID = 8726917363  # Stream bot numeric ID (filter ke liye)
FORCE_JOIN_CHANNEL = os.environ.get("FORCE_JOIN_CHANNEL", "")
DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", 10))

SETTINGS_FILE = "settings.json"
DB_FILE = "bot.db"

WAITING_CAPTION = 1
WAITING_THUMBNAIL = 2
WAITING_BROADCAST = 3
WAITING_WELCOME = 4
WAITING_BAN_ID = 5
WAITING_UNBAN_ID = 6
WAITING_LIMIT = 7

# ============================================================
#                    DATABASE SETUP
# ============================================================

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        joined_at TEXT,
        is_banned INTEGER DEFAULT 0,
        total_downloads INTEGER DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS downloads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        link TEXT,
        downloaded_at TEXT
    )''')
    conn.commit()
    conn.close()

def add_user(user_id, username, first_name):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''INSERT OR IGNORE INTO users (user_id, username, first_name, joined_at)
                 VALUES (?, ?, ?, ?)''',
              (user_id, username or "", first_name or "", datetime.now().isoformat()))
    conn.commit()
    conn.close()

def is_banned(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row and row[0] == 1

def ban_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def unban_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def get_today_downloads(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = datetime.now().date().isoformat()
    c.execute('''SELECT COUNT(*) FROM downloads
                 WHERE user_id = ? AND downloaded_at LIKE ?''',
              (user_id, f"{today}%"))
    count = c.fetchone()[0]
    conn.close()
    return count

def log_download(user_id, link):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO downloads (user_id, link, downloaded_at) VALUES (?, ?, ?)",
              (user_id, link, datetime.now().isoformat()))
    c.execute("UPDATE users SET total_downloads = total_downloads + 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE is_banned = 0")
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]

def get_stats():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1")
    banned = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM downloads")
    total_dl = c.fetchone()[0]
    today = datetime.now().date().isoformat()
    c.execute("SELECT COUNT(*) FROM downloads WHERE downloaded_at LIKE ?", (f"{today}%",))
    today_dl = c.fetchone()[0]
    c.execute("SELECT u.first_name, u.user_id, u.total_downloads FROM users u ORDER BY u.total_downloads DESC LIMIT 5")
    top_users = c.fetchall()
    conn.close()
    return total_users, banned, total_dl, today_dl, top_users

# ============================================================
#                    SETTINGS MANAGER
# ============================================================

def load_settings():
    default = {
        "caption": "🎬 *{filename}*\n\n▶️ Stream Link: {stream_link}\n\n💫 Enjoy!",
        "thumbnail": None,
        "welcome_msg": "👋 *Welcome!*\n\nMujhe Diskwala link bhejo, main stream link bhej dunga!\n\n🔗 Format:\n`https://www.diskwala.com/app/XXXXXX`"
    }
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r') as f:
            saved = json.load(f)
            default.update(saved)
    return default

def save_settings(data):
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(data, f, indent=2)

settings = load_settings()
init_db()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# pending_requests: BookTherepybot se video ka wait
# Key: sent message id to BookBot, Value: user info
pending_requests = {}

# stream_pending: aishwariyaupdatesbot se stream link ka wait
# Key: forwarded message id to stream bot, Value: user info + filename
stream_pending = {}

download_queue = asyncio.Queue()

userbot = Client(
    "my_account",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING
)

bot_app = Application.builder().token(BOT_TOKEN).build()

# ============================================================
#                    HELPERS
# ============================================================

def is_admin(user_id):
    return user_id == ADMIN_ID

async def check_force_join(user_id, context):
    if not FORCE_JOIN_CHANNEL:
        return True
    try:
        member = await context.bot.get_chat_member(FORCE_JOIN_CHANNEL, user_id)
        return member.status not in ["left", "kicked"]
    except:
        return False

# ============================================================
#                    USER HANDLERS
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    add_user(user.id, user.username, user.first_name)

    if is_banned(user.id):
        await update.message.reply_text("🚫 Tu banned hai! Admin se contact kar.")
        return

    joined = await check_force_join(user.id, context)
    if not joined:
        keyboard = [[InlineKeyboardButton("📢 Channel Join Karo", url=f"https://t.me/{FORCE_JOIN_CHANNEL.lstrip('@')}")]]
        await update.message.reply_text(
            "⚠️ *Pehle hamara channel join karo!*\n\nJoin karne ke baad /start bhejo.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    await update.message.reply_text(
        settings["welcome_msg"],
        parse_mode=ParseMode.MARKDOWN
    )

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_id = user.id
    text = update.message.text.strip()

    add_user(user_id, user.username, user.first_name)

    if is_banned(user_id):
        await update.message.reply_text("🚫 Tu banned hai!")
        return

    joined = await check_force_join(user_id, context)
    if not joined:
        keyboard = [[InlineKeyboardButton("📢 Channel Join Karo", url=f"https://t.me/{FORCE_JOIN_CHANNEL.lstrip('@')}")]]
        await update.message.reply_text(
            "⚠️ Pehle channel join karo!",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if not is_admin(user_id):
        today_count = get_today_downloads(user_id)
        if today_count >= DAILY_LIMIT:
            await update.message.reply_text(
                f"⚠️ *Daily limit reach ho gayi!*\n\n"
                f"Aaj ke liye maximum {DAILY_LIMIT} downloads allowed hain.\n"
                f"Kal dobara try karo! 🙏",
                parse_mode=ParseMode.MARKDOWN
            )
            return

    import re
    if not re.match(r'https?://www\.diskwala\.com/app/[a-zA-Z0-9]+', text):
        await update.message.reply_text(
            "❌ Valid Diskwala link nahi hai!\nFormat: `https://www.diskwala.com/app/XXXXXX`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    queue_pos = download_queue.qsize() + 1
    if queue_pos > 1:
        msg = await update.message.reply_text(
            f"📋 *Queue mein hai tera request!*\n\n🔢 Position: #{queue_pos}\n⏳ Thoda wait karo...",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        msg = await update.message.reply_text("⏳ Processing... thoda wait karo!")

    await download_queue.put({
        "user_id": user_id,
        "link": text,
        "msg_id": msg.message_id,
        "chat_id": update.message.chat_id
    })

# ============================================================
#                    QUEUE PROCESSOR
# ============================================================

async def process_queue():
    while True:
        item = await download_queue.get()
        user_id = item["user_id"]
        link = item["link"]
        msg_id = item["msg_id"]
        chat_id = item["chat_id"]

        try:
            await bot_app.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text="⏳ Video fetch kar raha hoon..."
            )
            sent = await userbot.send_message(BOT_B_USERNAME, link)
            pending_requests[sent.id] = {
                "user_chat_id": chat_id,
                "user_id": user_id,
                "processing_msg_id": msg_id,
                "link": link
            }
        except Exception as e:
            logger.error(f"Queue error: {e}")
            await bot_app.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text="❌ Error aa gaya! Dobara try karo."
            )
        finally:
            download_queue.task_done()

# ============================================================
#    STEP 1: BookTherepybot se video milna
#    → Video ko aishwariyaupdatesbot ko forward karo
# ============================================================

@userbot.on_message(filters.user(BOT_B_USERNAME) & filters.video)
async def receive_video_from_bookbot(client: Client, message: Message):
    if not pending_requests:
        logger.warning("Video aayi par pending_requests khali hai!")
        return

    oldest_key = next(iter(pending_requests))
    req = pending_requests.pop(oldest_key)
    user_chat_id = req["user_chat_id"]
    user_id = req["user_id"]
    proc_msg_id = req["processing_msg_id"]
    link = req["link"]

    try:
        await bot_app.bot.edit_message_text(
            chat_id=user_chat_id,
            message_id=proc_msg_id,
            text="📤 Stream link generate ho rahi hai... thoda ruko!"
        )

        filename = (message.video.file_name or "video.mp4").replace("Diskwala_File_", "").replace(".mp4", "")

        # Video ko stream bot ko forward karo
        forwarded = await message.forward(STREAM_BOT_USERNAME)

        # Stream bot ke reply ka wait karenge stream_pending mein
        stream_pending[forwarded.id] = {
            "user_chat_id": user_chat_id,
            "user_id": user_id,
            "processing_msg_id": proc_msg_id,
            "link": link,
            "filename": filename
        }

        logger.info(f"Video forwarded to stream bot. forwarded.id={forwarded.id}")

    except Exception as e:
        logger.error(f"Stream bot ko forward karne mein error: {e}")
        await bot_app.bot.edit_message_text(
            chat_id=user_chat_id,
            message_id=proc_msg_id,
            text="❌ Stream link generate nahi hui! Dobara try karo."
        )

# ============================================================
#    STEP 2: aishwariyaupdatesbot se stream link milna
#    → User ko link bhejo
# ============================================================

@userbot.on_message(filters.user(STREAM_BOT_ID) & filters.text)
async def receive_stream_link(client: Client, message: Message):
    if not stream_pending:
        logger.warning("Stream bot ne message bheja par stream_pending khali hai!")
        return

    import re
    text = message.text or ""
    urls = re.findall(r'https?://\S+', text)

    if not urls:
        logger.warning(f"Stream bot ke message mein koi link nahi mila: {text}")
        return

    stream_link = urls[0]

    oldest_key = next(iter(stream_pending))
    req = stream_pending.pop(oldest_key)

    user_chat_id = req["user_chat_id"]
    user_id = req["user_id"]
    proc_msg_id = req["processing_msg_id"]
    link = req["link"]
    filename = req["filename"]

    try:
        caption = settings["caption"].format(
            filename=filename,
            stream_link=stream_link
        )

        await bot_app.bot.send_message(
            chat_id=user_chat_id,
            text=caption,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=False
        )

        try:
            await bot_app.bot.delete_message(chat_id=user_chat_id, message_id=proc_msg_id)
        except:
            pass

        log_download(user_id, link)

        await bot_app.bot.send_message(
            ADMIN_ID,
            f"📥 *New Stream Request!*\n\n"
            f"👤 User ID: `{user_id}`\n"
            f"🎬 File: `{filename}`\n"
            f"🔗 Diskwala: `{link}`\n"
            f"▶️ Stream: {stream_link}\n"
            f"📅 Time: `{datetime.now().strftime('%d/%m/%Y %H:%M')}`",
            parse_mode=ParseMode.MARKDOWN
        )

        logger.info(f"Stream link user {user_id} ko bhej di: {stream_link}")

    except Exception as e:
        logger.error(f"User ko stream link bhejne mein error: {e}")
        try:
            await bot_app.bot.edit_message_text(
                chat_id=user_chat_id,
                message_id=proc_msg_id,
                text="❌ Stream link bhejne mein error! Dobara try karo."
            )
        except:
            pass

# ============================================================
#                    ADMIN PANEL
# ============================================================

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.message.chat_id):
        await update.message.reply_text("❌ Tu admin nahi hai!")
        return

    keyboard = [
        [InlineKeyboardButton("✏️ Caption", callback_data="admin_caption"),
         InlineKeyboardButton("🖼️ Thumbnail", callback_data="admin_thumbnail")],
        [InlineKeyboardButton("👋 Welcome Msg", callback_data="admin_welcome"),
         InlineKeyboardButton("👁️ Settings", callback_data="admin_view")],
        [InlineKeyboardButton("🗑️ Thumbnail Hata", callback_data="admin_remove_thumb"),
         InlineKeyboardButton("📊 Stats", callback_data="admin_stats")],
        [InlineKeyboardButton("🚫 User Ban", callback_data="admin_ban"),
         InlineKeyboardButton("✅ User Unban", callback_data="admin_unban")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"),
         InlineKeyboardButton("⚙️ Daily Limit", callback_data="admin_limit")],
    ]

    await update.message.reply_text(
        "🔧 *Admin Panel*\n\nKya karna hai?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_admin(query.from_user.id):
        await query.edit_message_text("❌ Tu admin nahi hai!")
        return ConversationHandler.END

    data = query.data

    if data == "admin_caption":
        context.user_data["admin_action"] = "caption"
        await query.edit_message_text(
            "✏️ *Naya Caption Bhej*\n\n"
            "💡 Variables:\n"
            "`{filename}` = video ka naam\n"
            "`{stream_link}` = stream link\n\n"
            "Example:\n`🎬 {filename}\n\n▶️ {stream_link}`\n\n/cancel",
            parse_mode=ParseMode.MARKDOWN
        )
        return WAITING_CAPTION

    elif data == "admin_thumbnail":
        context.user_data["admin_action"] = "thumbnail"
        await query.edit_message_text(
            "🖼️ *Naya Thumbnail Bhej*\n\nKoi bhi photo bhej!\n\n/cancel",
            parse_mode=ParseMode.MARKDOWN
        )
        return WAITING_THUMBNAIL

    elif data == "admin_welcome":
        context.user_data["admin_action"] = "welcome"
        await query.edit_message_text(
            "👋 *Naya Welcome Message Bhej*\n\n/cancel",
            parse_mode=ParseMode.MARKDOWN
        )
        return WAITING_WELCOME

    elif data == "admin_view":
        thumb_status = "✅ Set hai" if settings.get("thumbnail") and os.path.exists(settings["thumbnail"]) else "❌ Nahi"
        await query.edit_message_text(
            f"👁️ *Current Settings*\n\n"
            f"📝 *Caption:*\n`{settings['caption']}`\n\n"
            f"🖼️ *Thumbnail:* {thumb_status}\n\n"
            f"👋 *Welcome:*\n`{settings['welcome_msg']}`\n\n"
            f"📥 *Daily Limit:* {DAILY_LIMIT} downloads\n\n"
            f"📢 *Force Join:* {FORCE_JOIN_CHANNEL or 'Off'}",
            parse_mode=ParseMode.MARKDOWN
        )
        return ConversationHandler.END

    elif data == "admin_remove_thumb":
        settings["thumbnail"] = None
        save_settings(settings)
        await query.edit_message_text("✅ Thumbnail remove ho gayi!")
        return ConversationHandler.END

    elif data == "admin_stats":
        total_users, banned, total_dl, today_dl, top_users = get_stats()
        top_text = "\n".join([f"  {i+1}. {u[0]} — {u[2]} downloads" for i, u in enumerate(top_users)])
        await query.edit_message_text(
            f"📊 *Bot Stats*\n\n"
            f"👥 Total Users: `{total_users}`\n"
            f"🚫 Banned: `{banned}`\n"
            f"📥 Total Downloads: `{total_dl}`\n"
            f"📅 Aaj Downloads: `{today_dl}`\n\n"
            f"🏆 *Top Users:*\n{top_text}",
            parse_mode=ParseMode.MARKDOWN
        )
        return ConversationHandler.END

    elif data == "admin_ban":
        context.user_data["admin_action"] = "ban"
        await query.edit_message_text(
            "🚫 *Ban Karna Hai?*\n\nUser ka Telegram ID bhej:\n\n/cancel",
            parse_mode=ParseMode.MARKDOWN
        )
        return WAITING_BAN_ID

    elif data == "admin_unban":
        context.user_data["admin_action"] = "unban"
        await query.edit_message_text(
            "✅ *Unban Karna Hai?*\n\nUser ka Telegram ID bhej:\n\n/cancel",
            parse_mode=ParseMode.MARKDOWN
        )
        return WAITING_UNBAN_ID

    elif data == "admin_broadcast":
        context.user_data["admin_action"] = "broadcast"
        await query.edit_message_text(
            "📢 *Broadcast Message*\n\nJo message bhejega woh sabhi users ko jayega!\n\nMessage bhej:\n\n/cancel",
            parse_mode=ParseMode.MARKDOWN
        )
        return WAITING_BROADCAST

    elif data == "admin_limit":
        await query.edit_message_text(
            f"⚙️ *Daily Limit Change*\n\nAbhi: `{DAILY_LIMIT}` downloads/day\n\nNaya number bhej:\n\n/cancel",
            parse_mode=ParseMode.MARKDOWN
        )
        return WAITING_LIMIT

# ============================================================
#                CONVERSATION HANDLERS
# ============================================================

async def receive_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.message.chat_id):
        return ConversationHandler.END

    action = context.user_data.get("admin_action")

    if action == "caption":
        settings["caption"] = update.message.text
        save_settings(settings)
        await update.message.reply_text(
            f"✅ *Caption update!*\n\nPreview:\n`{settings['caption']}`",
            parse_mode=ParseMode.MARKDOWN
        )
    elif action == "welcome":
        settings["welcome_msg"] = update.message.text
        save_settings(settings)
        await update.message.reply_text("✅ *Welcome message update!*", parse_mode=ParseMode.MARKDOWN)

    return ConversationHandler.END

async def receive_thumbnail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.message.chat_id):
        return ConversationHandler.END
    if not update.message.photo:
        await update.message.reply_text("❌ Photo bhej!")
        return WAITING_THUMBNAIL
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    await file.download_to_drive("thumbnail.jpg")
    settings["thumbnail"] = "thumbnail.jpg"
    save_settings(settings)
    await update.message.reply_text("✅ *Thumbnail set!*", parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END

async def receive_ban_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.message.chat_id):
        return ConversationHandler.END
    try:
        uid = int(update.message.text.strip())
        ban_user(uid)
        await update.message.reply_text(f"🚫 User `{uid}` ban ho gaya!", parse_mode=ParseMode.MARKDOWN)
        try:
            await context.bot.send_message(uid, "🚫 Tumhe bot se ban kar diya gaya hai. Admin se contact karo.")
        except:
            pass
    except:
        await update.message.reply_text("❌ Valid User ID daalo (sirf number)")
    return ConversationHandler.END

async def receive_unban_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.message.chat_id):
        return ConversationHandler.END
    try:
        uid = int(update.message.text.strip())
        unban_user(uid)
        await update.message.reply_text(f"✅ User `{uid}` unban ho gaya!", parse_mode=ParseMode.MARKDOWN)
        try:
            await context.bot.send_message(uid, "✅ Tumhara ban hat gaya! Ab bot use kar sakte ho.")
        except:
            pass
    except:
        await update.message.reply_text("❌ Valid User ID daalo")
    return ConversationHandler.END

async def receive_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.message.chat_id):
        return ConversationHandler.END

    broadcast_text = update.message.text
    users = get_all_users()
    success = 0
    failed = 0

    status_msg = await update.message.reply_text(f"📢 Broadcast shuru... 0/{len(users)}")

    for i, uid in enumerate(users):
        try:
            await context.bot.send_message(uid, broadcast_text, parse_mode=ParseMode.MARKDOWN)
            success += 1
        except:
            failed += 1
        if (i + 1) % 10 == 0:
            await status_msg.edit_text(f"📢 Bhej raha hoon... {i+1}/{len(users)}")
        await asyncio.sleep(0.05)

    await status_msg.edit_text(
        f"✅ *Broadcast Complete!*\n\n"
        f"✅ Sent: `{success}`\n"
        f"❌ Failed: `{failed}`",
        parse_mode=ParseMode.MARKDOWN
    )
    return ConversationHandler.END

async def receive_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.message.chat_id):
        return ConversationHandler.END
    global DAILY_LIMIT
    try:
        DAILY_LIMIT = int(update.message.text.strip())
        await update.message.reply_text(f"✅ Daily limit `{DAILY_LIMIT}` set ho gayi!", parse_mode=ParseMode.MARKDOWN)
    except:
        await update.message.reply_text("❌ Valid number daalo!")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Cancelled!")
    return ConversationHandler.END

# ============================================================
#                         MAIN
# ============================================================

async def main():
    admin_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_callback, pattern="^admin_")],
        states={
            WAITING_CAPTION: [MessageHandler(tg_filters.TEXT & ~tg_filters.COMMAND, receive_text_input)],
            WAITING_THUMBNAIL: [MessageHandler(tg_filters.PHOTO, receive_thumbnail)],
            WAITING_WELCOME: [MessageHandler(tg_filters.TEXT & ~tg_filters.COMMAND, receive_text_input)],
            WAITING_BAN_ID: [MessageHandler(tg_filters.TEXT & ~tg_filters.COMMAND, receive_ban_id)],
            WAITING_UNBAN_ID: [MessageHandler(tg_filters.TEXT & ~tg_filters.COMMAND, receive_unban_id)],
            WAITING_BROADCAST: [MessageHandler(tg_filters.TEXT & ~tg_filters.COMMAND, receive_broadcast)],
            WAITING_LIMIT: [MessageHandler(tg_filters.TEXT & ~tg_filters.COMMAND, receive_limit)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
    )

    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("admin", admin_panel))
    bot_app.add_handler(admin_conv)
    bot_app.add_handler(MessageHandler(tg_filters.TEXT & ~tg_filters.COMMAND, handle_link))

    # Sahi order mein initialize
    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling()

    asyncio.create_task(process_queue())

    await userbot.start()

    logger.info("✅ Bot chal raha hai!")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import logging
import os
import json
import sqlite3
from datetime import datetime
from collections import deque
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

BOT_TOKEN       = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")
API_ID          = int(os.environ.get("API_ID", 123456))
API_HASH        = os.environ.get("API_HASH", "your_api_hash")
SESSION_STRING  = os.environ.get("SESSION_STRING", "")
ADMIN_ID        = int(os.environ.get("ADMIN_ID", 123456789))
FORCE_JOIN      = os.environ.get("FORCE_JOIN_CHANNEL", "")
DAILY_LIMIT     = int(os.environ.get("DAILY_LIMIT", 10))

BOT_B_USERNAME  = "BookTherepybot"
DB_CHANNEL_ID   = -1003618041359   # Tera private database channel

SETTINGS_FILE   = "settings.json"
DB_FILE         = "bot.db"

# Conversation states
WAITING_CAPTION    = 1
WAITING_THUMBNAIL  = 2
WAITING_WELCOME    = 3
WAITING_BAN_ID     = 4
WAITING_UNBAN_ID   = 5
WAITING_BROADCAST  = 6
WAITING_LIMIT      = 7

# ============================================================
#                    QUEUE
# ============================================================

# Har item: { user_id, chat_id, link, msg_id }
request_queue = deque()
is_processing = False
queue_lock = asyncio.Lock()

# Channel video event globals
pending_channel_event = None
pending_channel_result = None

# ============================================================
#                    DATABASE
# ============================================================

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT, first_name TEXT,
        joined_at TEXT, is_banned INTEGER DEFAULT 0,
        total_downloads INTEGER DEFAULT 0
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS downloads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, link TEXT, downloaded_at TEXT
    )''')
    conn.commit()
    conn.close()

def add_user(user_id, username, first_name):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''INSERT OR IGNORE INTO users
        (user_id, username, first_name, joined_at)
        VALUES (?, ?, ?, ?)''',
        (user_id, username or "", first_name or "", datetime.now().isoformat()))
    conn.commit()
    conn.close()

def is_banned(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT is_banned FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row and row[0] == 1

def ban_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET is_banned=1 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def unban_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET is_banned=0 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def get_today_downloads(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    today = datetime.now().date().isoformat()
    c.execute("SELECT COUNT(*) FROM downloads WHERE user_id=? AND downloaded_at LIKE ?",
              (user_id, f"{today}%"))
    count = c.fetchone()[0]
    conn.close()
    return count

def log_download(user_id, link):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO downloads (user_id, link, downloaded_at) VALUES (?, ?, ?)",
              (user_id, link, datetime.now().isoformat()))
    c.execute("UPDATE users SET total_downloads=total_downloads+1 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE is_banned=0")
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]

def get_stats():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE is_banned=1")
    banned = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM downloads")
    total_dl = c.fetchone()[0]
    today = datetime.now().date().isoformat()
    c.execute("SELECT COUNT(*) FROM downloads WHERE downloaded_at LIKE ?", (f"{today}%",))
    today_dl = c.fetchone()[0]
    c.execute("SELECT first_name, user_id, total_downloads FROM users ORDER BY total_downloads DESC LIMIT 5")
    top = c.fetchall()
    conn.close()
    return total, banned, total_dl, today_dl, top

# ============================================================
#                    SETTINGS
# ============================================================

def load_settings():
    default = {
        "caption": "🎬 *{filename}*\n\n📥 @YourBot\n💫 Enjoy!",
        "thumbnail": None,
        "welcome_msg": "👋 *Welcome!*\n\nDiskwala link bhejo!\n\n`https://www.diskwala.com/app/XXXXXX`"
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

userbot = Client(
    "my_account",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING
)

bot_app = Application.builder().token(BOT_TOKEN).build()

# ============================================================
#     USERBOT — TOP LEVEL CHANNEL LISTENER (Permanent Handler)
# ============================================================

@userbot.on_message(filters.video)
async def on_any_video(client, message):
    """Har video message catch karo — channel wala filter karenge"""
    global pending_channel_event, pending_channel_result

    # Sirf tera database channel ka video chahiye
    chat_id = message.chat.id
    # Pyrogram mein private channel ID negative hoti hai same as -100xxxxx
    if chat_id != DB_CHANNEL_ID:
        return

    if pending_channel_event is None:
        return  # Koi request nahi hai abhi

    # Video mil gayi!
    logger.info(f"✅ Channel pe video detect hui! msg_id={message.id}")
    pending_channel_result["id"] = message.id
    pending_channel_event.set()
    pending_channel_event = None
    pending_channel_result = None

# ============================================================
#                    HELPERS
# ============================================================

def is_admin(user_id):
    return user_id == ADMIN_ID

async def check_force_join(user_id, context):
    if not FORCE_JOIN:
        return True
    try:
        member = await context.bot.get_chat_member(FORCE_JOIN, user_id)
        return member.status not in ["left", "kicked"]
    except:
        return False

async def update_queue_messages():
    """Jab koi complete ho — baaki sabke position messages update karo"""
    for i, item in enumerate(request_queue):
        try:
            pos = i + 1
            if pos == 1:
                text = "⏳ *Tera number aa gaya!*\n\nProcess ho raha hai..."
            else:
                text = (
                    f"📋 *Queue mein hai!*\n\n"
                    f"🔢 Position: *#{pos}*\n"
                    f"⏳ {pos-1} request{'s' if pos > 2 else ''} pehle\n\n"
                    f"_Apni baari aate hi process hoga!_"
                )
            await bot_app.bot.edit_message_text(
                chat_id=item["chat_id"],
                message_id=item["msg_id"],
                text=text,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.warning(f"Queue msg update failed: {e}")

# ============================================================
#         USERBOT — CHANNEL PE VIDEO DETECT KARO (TOP LEVEL)
# ============================================================

# Pyrogram channel ID = -100 hatao
PYROGRAM_CHANNEL_ID = int(str(DB_CHANNEL_ID).replace("-100", ""))

# ============================================================
#                    QUEUE PROCESSOR
# ============================================================

async def process_next_in_queue():
    """Queue ka pehla item process karo"""
    global is_processing

    async with queue_lock:
        if is_processing or len(request_queue) == 0:
            return
        is_processing = True

    item = request_queue.popleft()
    user_id  = item["user_id"]
    chat_id  = item["chat_id"]
    link     = item["link"]
    msg_id   = item["msg_id"]

    logger.info(f"Processing: user={user_id} | queue left={len(request_queue)}")

    try:
        # Baaki sab ke positions update karo
        await update_queue_messages()

        await bot_app.bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text="⏳ *Bot B ko link bhej raha hoon...*",
            parse_mode=ParseMode.MARKDOWN
        )

        # Event + asyncio queue for channel video
        video_received = asyncio.Event()
        channel_msg_id = {}

        # ── Permanent handler se signal lenge (registered at top level) ──
        # Yahan ek asyncio Event use karenge
        # Global pending_channel_event set karo
        global pending_channel_event, pending_channel_result
        pending_channel_event = video_received
        pending_channel_result = channel_msg_id

        # Bot B ko link bhejo
        await userbot.send_message(BOT_B_USERNAME, link)

        await bot_app.bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text="📨 *Link bhej diya!*\n\nVideo aa rahi hai...",
            parse_mode=ParseMode.MARKDOWN
        )

        # Max 3 min wait
        try:
            await asyncio.wait_for(video_received.wait(), timeout=180)
        except asyncio.TimeoutError:
            pending_channel_event = None
            pending_channel_result = None
            await bot_app.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text="❌ *Timeout!*\n\nBot B ne 3 min mein video nahi bheji.\nDobara try karo.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # ── Video channel pe aa gayi! ──
        ch_msg_id = channel_msg_id["id"]

        # Channel message ka link generate karo
        # Private channel link format: https://t.me/c/CHANNEL_ID/MSG_ID
        # Channel ID se -100 hatao
        clean_channel_id = str(DB_CHANNEL_ID).replace("-100", "")
        video_link = f"https://t.me/c/{clean_channel_id}/{ch_msg_id}"

        logger.info(f"Video on channel! msg_id={ch_msg_id}, link={video_link}")

        # ── QUEUE KA AGLA LINK TURANT BHEJO ──
        # User ka wait nahi karenge — agla process shuru!
        async with queue_lock:
            is_processing = False

        if len(request_queue) > 0:
            asyncio.create_task(process_next_in_queue())

        # ── User ko button do ──
        keyboard = [[
            InlineKeyboardButton("📥 Video Download Karo", callback_data=f"get_video:{ch_msg_id}:{user_id}")
        ]]

        await bot_app.bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=(
                f"✅ *Video Ready Hai!*\n\n"
                f"Neeche button dabao — video mil jaayegi! 👇"
            ),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

        # Log karo
        log_download(user_id, link)

        # Admin notify
        try:
            await bot_app.bot.send_message(
                ADMIN_ID,
                f"📥 *New Download!*\n\n"
                f"👤 User: `{user_id}`\n"
                f"🔗 Link: `{link}`\n"
                f"📅 Time: `{datetime.now().strftime('%d/%m %H:%M')}`\n"
                f"📋 Queue left: `{len(request_queue)}`",
                parse_mode=ParseMode.MARKDOWN
            )
        except:
            pass

    except Exception as e:
        logger.error(f"Processing error: {e}")
        async with queue_lock:
            is_processing = False
        try:
            await bot_app.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text="❌ *Error aa gaya!*\n\nDobara link bhejo.",
                parse_mode=ParseMode.MARKDOWN
            )
        except:
            pass
        # Error pe bhi agla process karo
        if len(request_queue) > 0:
            asyncio.create_task(process_next_in_queue())


# ============================================================
#                    BUTTON HANDLER — VIDEO DELIVERY
# ============================================================

async def handle_video_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("📥 Video bhej raha hoon...")

    data = query.data  # get_video:CH_MSG_ID:USER_ID
    parts = data.split(":")
    ch_msg_id = int(parts[1])
    original_user_id = int(parts[2])

    # Sirf wahi user download kar sakta hai
    if query.from_user.id != original_user_id:
        await query.answer("❌ Yeh video sirf us user ke liye hai jisne link bheja tha!", show_alert=True)
        return

    chat_id = query.message.chat_id

    try:
        # Button message update karo
        await query.edit_message_text(
            "📥 *Video bhej raha hoon...*\n\nThoda wait karo!",
            parse_mode=ParseMode.MARKDOWN
        )

        # Channel se video download karo
        channel_message = await userbot.get_messages(DB_CHANNEL_ID, ch_msg_id)
        video_path = await channel_message.download()

        filename = (channel_message.video.file_name or "video.mp4").replace("Diskwala_File_", "").replace(".mp4", "")
        caption = settings["caption"].format(filename=filename)

        thumb_path = settings.get("thumbnail")
        thumb = open(thumb_path, 'rb') if (thumb_path and os.path.exists(thumb_path)) else None

        # User ko video bhejo
        with open(video_path, 'rb') as vf:
            await bot_app.bot.send_video(
                chat_id=chat_id,
                video=vf,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN,
                thumbnail=thumb,
                supports_streaming=True
            )

        if thumb:
            thumb.close()

        # Button message delete karo
        await bot_app.bot.delete_message(chat_id=chat_id, message_id=query.message.message_id)
        os.remove(video_path)

        logger.info(f"Video delivered to user {original_user_id}")

    except Exception as e:
        logger.error(f"Video delivery error: {e}")
        await bot_app.bot.send_message(
            chat_id=chat_id,
            text="❌ *Video bhejne mein error!*\n\nDobara button dabao ya link bhejo.",
            parse_mode=ParseMode.MARKDOWN
        )

# ============================================================
#                    USER HANDLERS
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    add_user(user.id, user.username, user.first_name)

    if is_banned(user.id):
        await update.message.reply_text("🚫 Tu banned hai!")
        return

    joined = await check_force_join(user.id, context)
    if not joined:
        kb = [[InlineKeyboardButton("📢 Channel Join Karo", url=f"https://t.me/{FORCE_JOIN.lstrip('@')}")]]
        await update.message.reply_text(
            "⚠️ *Pehle channel join karo!*\n\nJoin ke baad /start bhejo.",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    await update.message.reply_text(settings["welcome_msg"], parse_mode=ParseMode.MARKDOWN)


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
        kb = [[InlineKeyboardButton("📢 Channel Join Karo", url=f"https://t.me/{FORCE_JOIN.lstrip('@')}")]]
        await update.message.reply_text("⚠️ Pehle channel join karo!", reply_markup=InlineKeyboardMarkup(kb))
        return

    if not is_admin(user_id):
        if get_today_downloads(user_id) >= DAILY_LIMIT:
            await update.message.reply_text(
                f"⚠️ *Daily limit!*\n\nAaj ke {DAILY_LIMIT} downloads ho gaye. Kal aana! 🙏",
                parse_mode=ParseMode.MARKDOWN
            )
            return

    import re
    if not re.match(r'https?://www\.diskwala\.com/app/[a-zA-Z0-9]+', text):
        await update.message.reply_text(
            "❌ Valid Diskwala link chahiye!\n`https://www.diskwala.com/app/XXXXXX`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    queue_pos = len(request_queue) + (1 if is_processing else 0) + 1

    if queue_pos == 1 and not is_processing:
        msg = await update.message.reply_text("⏳ *Processing shuru...*", parse_mode=ParseMode.MARKDOWN)
    else:
        msg = await update.message.reply_text(
            f"📋 *Queue mein add ho gaya!*\n\n"
            f"🔢 Position: *#{queue_pos}*\n"
            f"⏳ {queue_pos-1} request{'s' if queue_pos > 2 else ''} pehle hain\n\n"
            f"_Apni baari aate hi process hoga!_",
            parse_mode=ParseMode.MARKDOWN
        )

    request_queue.append({
        "user_id": user_id,
        "chat_id": update.message.chat_id,
        "link": text,
        "msg_id": msg.message_id
    })

    if not is_processing:
        asyncio.create_task(process_next_in_queue())

# ============================================================
#                    ADMIN PANEL
# ============================================================

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.message.chat_id):
        await update.message.reply_text("❌ Tu admin nahi hai!")
        return

    proc = "🟢 Chal raha" if is_processing else "🔴 Idle"
    kb = [
        [InlineKeyboardButton("✏️ Caption", callback_data="admin_caption"),
         InlineKeyboardButton("🖼️ Thumbnail", callback_data="admin_thumbnail")],
        [InlineKeyboardButton("👋 Welcome Msg", callback_data="admin_welcome"),
         InlineKeyboardButton("👁️ Settings", callback_data="admin_view")],
        [InlineKeyboardButton("🗑️ Thumbnail Hata", callback_data="admin_remove_thumb"),
         InlineKeyboardButton("📊 Stats", callback_data="admin_stats")],
        [InlineKeyboardButton("🚫 Ban", callback_data="admin_ban"),
         InlineKeyboardButton("✅ Unban", callback_data="admin_unban")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"),
         InlineKeyboardButton("⚙️ Daily Limit", callback_data="admin_limit")],
        [InlineKeyboardButton("📋 Queue Status", callback_data="admin_queue")],
    ]

    await update.message.reply_text(
        f"🔧 *Admin Panel*\n\n"
        f"⚙️ Status: {proc}\n"
        f"📋 Queue: `{len(request_queue)}` requests\n\n"
        f"Kya karna hai?",
        reply_markup=InlineKeyboardMarkup(kb),
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
            "✏️ *Naya Caption Bhej*\n\n`{filename}` = video naam\n\n/cancel",
            parse_mode=ParseMode.MARKDOWN
        )
        return WAITING_CAPTION

    elif data == "admin_thumbnail":
        context.user_data["admin_action"] = "thumbnail"
        await query.edit_message_text("🖼️ *Photo Bhej Thumbnail Ke Liye*\n\n/cancel", parse_mode=ParseMode.MARKDOWN)
        return WAITING_THUMBNAIL

    elif data == "admin_welcome":
        context.user_data["admin_action"] = "welcome"
        await query.edit_message_text("👋 *Naya Welcome Message Bhej*\n\n/cancel", parse_mode=ParseMode.MARKDOWN)
        return WAITING_WELCOME

    elif data == "admin_view":
        thumb = "✅ Set" if settings.get("thumbnail") and os.path.exists(settings["thumbnail"]) else "❌ Nahi"
        await query.edit_message_text(
            f"👁️ *Current Settings*\n\n"
            f"📝 Caption:\n`{settings['caption']}`\n\n"
            f"🖼️ Thumbnail: {thumb}\n\n"
            f"👋 Welcome:\n`{settings['welcome_msg']}`\n\n"
            f"📥 Daily Limit: `{DAILY_LIMIT}`\n"
            f"📢 Force Join: `{FORCE_JOIN or 'Off'}`",
            parse_mode=ParseMode.MARKDOWN
        )
        return ConversationHandler.END

    elif data == "admin_remove_thumb":
        settings["thumbnail"] = None
        save_settings(settings)
        await query.edit_message_text("✅ Thumbnail remove ho gayi!")
        return ConversationHandler.END

    elif data == "admin_stats":
        total, banned, total_dl, today_dl, top = get_stats()
        top_text = "\n".join([f"  {i+1}. {u[0]} — {u[2]} dl" for i, u in enumerate(top)])
        await query.edit_message_text(
            f"📊 *Stats*\n\n"
            f"👥 Users: `{total}`\n"
            f"🚫 Banned: `{banned}`\n"
            f"📥 Total DL: `{total_dl}`\n"
            f"📅 Aaj: `{today_dl}`\n\n"
            f"🏆 Top:\n{top_text}",
            parse_mode=ParseMode.MARKDOWN
        )
        return ConversationHandler.END

    elif data == "admin_queue":
        if len(request_queue) == 0 and not is_processing:
            qt = "Queue khali hai! 🎉"
        else:
            lines = []
            if is_processing:
                lines.append("🔄 *Process ho raha hai:* 1 request")
            for i, itm in enumerate(request_queue):
                lines.append(f"  #{i+1} — User `{itm['user_id']}`")
            qt = "\n".join(lines)
        await query.edit_message_text(f"📋 *Queue Status*\n\n{qt}", parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END

    elif data == "admin_ban":
        context.user_data["admin_action"] = "ban"
        await query.edit_message_text("🚫 *Ban Karna Hai?*\n\nUser ID bhej:\n\n/cancel", parse_mode=ParseMode.MARKDOWN)
        return WAITING_BAN_ID

    elif data == "admin_unban":
        context.user_data["admin_action"] = "unban"
        await query.edit_message_text("✅ *Unban Karna Hai?*\n\nUser ID bhej:\n\n/cancel", parse_mode=ParseMode.MARKDOWN)
        return WAITING_UNBAN_ID

    elif data == "admin_broadcast":
        await query.edit_message_text("📢 *Broadcast Message Bhej*\n\nSab users ko jayega!\n\n/cancel", parse_mode=ParseMode.MARKDOWN)
        return WAITING_BROADCAST

    elif data == "admin_limit":
        await query.edit_message_text(f"⚙️ *Daily Limit*\n\nAbhi: `{DAILY_LIMIT}`\n\nNaya number:\n\n/cancel", parse_mode=ParseMode.MARKDOWN)
        return WAITING_LIMIT

# ============================================================
#              CONVERSATION HANDLERS
# ============================================================

async def receive_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.message.chat_id):
        return ConversationHandler.END
    action = context.user_data.get("admin_action")
    if action == "caption":
        settings["caption"] = update.message.text
        save_settings(settings)
        await update.message.reply_text(f"✅ Caption update!\n\n{settings['caption']}", parse_mode=ParseMode.MARKDOWN)
    elif action == "welcome":
        settings["welcome_msg"] = update.message.text
        save_settings(settings)
        await update.message.reply_text("✅ Welcome msg update!", parse_mode=ParseMode.MARKDOWN)
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
    await update.message.reply_text("✅ Thumbnail set!", parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END

async def receive_ban_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.message.chat_id):
        return ConversationHandler.END
    try:
        uid = int(update.message.text.strip())
        ban_user(uid)
        await update.message.reply_text(f"🚫 User `{uid}` ban!", parse_mode=ParseMode.MARKDOWN)
        try:
            await context.bot.send_message(uid, "🚫 Tumhe ban kar diya gaya. Admin se contact karo.")
        except: pass
    except:
        await update.message.reply_text("❌ Valid ID daalo!")
    return ConversationHandler.END

async def receive_unban_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.message.chat_id):
        return ConversationHandler.END
    try:
        uid = int(update.message.text.strip())
        unban_user(uid)
        await update.message.reply_text(f"✅ User `{uid}` unban!", parse_mode=ParseMode.MARKDOWN)
        try:
            await context.bot.send_message(uid, "✅ Ban hat gaya! Bot use karo.")
        except: pass
    except:
        await update.message.reply_text("❌ Valid ID daalo!")
    return ConversationHandler.END

async def receive_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.message.chat_id):
        return ConversationHandler.END
    users = get_all_users()
    success = failed = 0
    status = await update.message.reply_text(f"📢 Shuru... 0/{len(users)}")
    for i, uid in enumerate(users):
        try:
            await context.bot.send_message(uid, update.message.text, parse_mode=ParseMode.MARKDOWN)
            success += 1
        except:
            failed += 1
        if (i+1) % 10 == 0:
            await status.edit_text(f"📢 {i+1}/{len(users)}...")
        await asyncio.sleep(0.05)
    await status.edit_text(
        f"✅ *Broadcast Done!*\n\n✅ {success}\n❌ {failed}",
        parse_mode=ParseMode.MARKDOWN
    )
    return ConversationHandler.END

async def receive_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.message.chat_id):
        return ConversationHandler.END
    global DAILY_LIMIT
    try:
        DAILY_LIMIT = int(update.message.text.strip())
        await update.message.reply_text(f"✅ Limit `{DAILY_LIMIT}` set!", parse_mode=ParseMode.MARKDOWN)
    except:
        await update.message.reply_text("❌ Number daalo!")
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
            WAITING_CAPTION:   [MessageHandler(tg_filters.TEXT & ~tg_filters.COMMAND, receive_text_input)],
            WAITING_THUMBNAIL: [MessageHandler(tg_filters.PHOTO, receive_thumbnail)],
            WAITING_WELCOME:   [MessageHandler(tg_filters.TEXT & ~tg_filters.COMMAND, receive_text_input)],
            WAITING_BAN_ID:    [MessageHandler(tg_filters.TEXT & ~tg_filters.COMMAND, receive_ban_id)],
            WAITING_UNBAN_ID:  [MessageHandler(tg_filters.TEXT & ~tg_filters.COMMAND, receive_unban_id)],
            WAITING_BROADCAST: [MessageHandler(tg_filters.TEXT & ~tg_filters.COMMAND, receive_broadcast)],
            WAITING_LIMIT:     [MessageHandler(tg_filters.TEXT & ~tg_filters.COMMAND, receive_limit)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
        per_chat=True,
    )

    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("admin", admin_panel))
    bot_app.add_handler(admin_conv)
    bot_app.add_handler(CallbackQueryHandler(handle_video_button, pattern="^get_video:"))
    bot_app.add_handler(MessageHandler(tg_filters.TEXT & ~tg_filters.COMMAND, handle_link))

    # Pehle initialize karo — phir start karo (order zaroori hai!)
    await bot_app.initialize()

    await asyncio.gather(
        userbot.start(),
        bot_app.start(),
        bot_app.updater.start_polling(),
    )

    logger.info("✅ Bot chal raha hai!")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())

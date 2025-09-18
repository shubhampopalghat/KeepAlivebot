import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ForceReply
from telegram.constants import ChatType, ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackContext,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


# --------------- Configuration and State ---------------

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
STATE_PATH = os.path.join(os.path.dirname(__file__), "state.json")

BROADCAST_INTERVAL_SECONDS = 7 * 60  # 7 minutes


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Improve asyncio compatibility on Windows
if os.name == "nt":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception as e:
        logger.debug("Failed to set WindowsSelectorEventLoopPolicy: %s", e)


@dataclass
class Config:
    bot_token: str
    owner_ids: List[int] = field(default_factory=list)


@dataclass
class BotState:
    groups: Dict[str, str] = field(default_factory=dict)  # chat_id -> title
    regular_message: str = "Hello everyone! Keeping the group active."
    broadcasts_enabled: bool = True
    interval_seconds: int = 7 * 60


class Storage:
    def __init__(self, state_path: str):
        self.path = state_path
        self._lock = asyncio.Lock()
        self.state = BotState()

    async def load(self) -> None:
        if not os.path.exists(self.path):
            await self.save()
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.state = BotState(
                groups=data.get("groups", {}),
                regular_message=data.get(
                    "regular_message", "Hello everyone! Keeping the group active."
                ),
                broadcasts_enabled=bool(data.get("broadcasts_enabled", True)),
                interval_seconds=int(data.get("interval_seconds", 7 * 60)),
            )
        except Exception as e:
            logger.error("Failed to load state: %s", e)

    async def save(self) -> None:
        async with self._lock:
            tmp_path = self.path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "groups": self.state.groups,
                        "regular_message": self.state.regular_message,
                        "broadcasts_enabled": self.state.broadcasts_enabled,
                        "interval_seconds": self.state.interval_seconds,
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            os.replace(tmp_path, self.path)


def ensure_config() -> Config:
    if not os.path.exists(CONFIG_PATH):
        # Create a template config for the user to fill in
        template = {
            "bot_token": "PUT_YOUR_BOT_TOKEN_HERE",
            "owner_ids": [123456789],
        }
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(template, f, indent=2)
        raise SystemExit(
            f"config.json created at {CONFIG_PATH}. Please fill in your bot_token and owner_ids, then run again."
        )

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    token = data.get("bot_token")
    owners = data.get("owner_ids", [])
    if not token or token == "PUT_YOUR_BOT_TOKEN_HERE":
        raise SystemExit("Please set a valid bot_token in config.json")
    try:
        owners = [int(x) for x in owners]
    except Exception:
        raise SystemExit("owner_ids must be a list of integers in config.json")

    return Config(bot_token=token, owner_ids=owners)


# --------------- Utility functions ---------------

def is_owner(user_id: Optional[int], cfg: Config) -> bool:
    return user_id is not None and user_id in cfg.owner_ids


async def add_group(chat_id: int, title: str, storage: Storage) -> None:
    chat_key = str(chat_id)
    if storage.state.groups.get(chat_key) != title:
        storage.state.groups[chat_key] = title
        await storage.save()
        logger.info("Added/updated group %s (%s)", title, chat_key)


async def remove_group(chat_id: int, storage: Storage) -> None:
    chat_key = str(chat_id)
    if chat_key in storage.state.groups:
        storage.state.groups.pop(chat_key, None)
        await storage.save()
        logger.info("Removed group %s", chat_key)


async def safe_send(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str) -> None:
    try:
        await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.warning("Failed to send to %s: %s", chat_id, e)


# --------------- Handlers ---------------


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.bot_data["config"]
    if update.effective_chat and update.effective_chat.type == ChatType.PRIVATE:
        if is_owner(update.effective_user.id, cfg):
            await show_owner_menu(update, context)
        else:
            text = "Hello! This bot keeps groups active when added."
            await update.effective_chat.send_message(text)
    else:
        # In groups, acknowledge presence
        await update.effective_message.reply_text(
            "I'm alive! I'll help keep this group active with periodic messages."
        )


async def show_owner_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send or update an inline keyboard menu for the owner."""
    cfg: Config = context.bot_data["config"]
    if not is_owner(update.effective_user.id, cfg):
        return
    storage: Storage = context.bot_data["storage"]
    st = storage.state
    status = "ON" if st.broadcasts_enabled else "OFF"
    kb = [
        [
            InlineKeyboardButton("Send Now", callback_data="owner:send_now"),
            InlineKeyboardButton("Edit Message", callback_data="owner:edit_msg"),
        ],
        [
            InlineKeyboardButton(
                f"Change Interval ({st.interval_seconds//60} min)", callback_data="owner:change_interval"
            ),
        ],
        [
            InlineKeyboardButton(f"Toggle ({status})", callback_data="owner:toggle"),
            InlineKeyboardButton("List Groups", callback_data="owner:list_groups"),
        ],
        [InlineKeyboardButton("Refresh", callback_data="owner:refresh")],
    ]
    text = (
        "Owner Panel\n\n"
        f"Regular message:\n<code>{st.regular_message}</code>\n\n"
        f"Interval: {st.interval_seconds//60} min\n"
        f"Periodic: {status}\n"
        f"Groups: {len(st.groups)}\n"
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    else:
        await update.effective_chat.send_message(text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)


async def on_bot_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    storage: Storage = context.bot_data["storage"]
    chat = update.effective_chat
    if not chat:
        return

    cmu = update.my_chat_member
    if not cmu:
        return

    new_status = cmu.new_chat_member.status
    old_status = cmu.old_chat_member.status

    if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        if new_status in ("member", "administrator"):
            await add_group(chat.id, chat.title or str(chat.id), storage)
        elif new_status in ("left", "kicked"):
            await remove_group(chat.id, storage)


async def list_groups(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.bot_data["config"]
    storage: Storage = context.bot_data["storage"]
    if not is_owner(update.effective_user.id, cfg):
        return
    items = [f"{title} (<code>{chat_id}</code>)" for chat_id, title in storage.state.groups.items()]
    text = "Tracked groups (" + str(len(items)) + "):\n" + ("\n".join(items) if items else "None")
    await update.effective_chat.send_message(text, parse_mode=ParseMode.HTML)


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_owner_menu(update, context)


async def handle_owner_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    cfg: Config = context.bot_data["config"]
    if not is_owner(query.from_user.id, cfg):
        return
    storage: Storage = context.bot_data["storage"]
    data = query.data or ""

    if data == "owner:send_now":
        # Ask owner for a custom message to broadcast now
        context.user_data["awaiting"] = "send_now_custom"
        await query.message.reply_text(
            "Send the custom broadcast message to send now:",
            reply_markup=ForceReply(selective=True),
        )
    elif data == "owner:edit_msg":
        context.user_data["awaiting"] = "edit_msg"
        await query.message.reply_text(
            "Send the new regular broadcast message:", reply_markup=ForceReply(selective=True)
        )
    elif data == "owner:change_interval":
        context.user_data["awaiting"] = "change_interval"
        await query.message.reply_text(
            "Send the new interval in minutes (e.g., 7):", reply_markup=ForceReply(selective=True)
        )
    elif data == "owner:toggle":
        storage.state.broadcasts_enabled = not storage.state.broadcasts_enabled
        await storage.save()
        await show_owner_menu(update, context)
    elif data == "owner:list_groups":
        items = [f"{title} (<code>{cid}</code>)" for cid, title in storage.state.groups.items()]
        text = "Tracked groups (" + str(len(items)) + "):\n" + ("\n".join(items) if items else "None")
        await query.message.reply_text(text, parse_mode=ParseMode.HTML)
        await show_owner_menu(update, context)
    elif data == "owner:refresh":
        await show_owner_menu(update, context)


async def owner_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Only act if owner and awaiting input
    cfg: Config = context.bot_data["config"]
    if update.effective_chat.type != ChatType.PRIVATE or not is_owner(update.effective_user.id, cfg):
        return
    awaiting = context.user_data.get("awaiting")
    if not awaiting:
        return
    storage: Storage = context.bot_data["storage"]
    text = (update.effective_message.text or "").strip()
    if awaiting == "edit_msg":
        if not text:
            await update.effective_chat.send_message("Message cannot be empty. Please send again.")
            return
        storage.state.regular_message = text
        await storage.save()
        context.user_data.pop("awaiting", None)
        await update.effective_chat.send_message("Regular broadcast message updated.")
        # Show menu
        await show_owner_menu(update, context)
    elif awaiting == "change_interval":
        try:
            minutes = int(text)
            if minutes <= 0 or minutes > 24 * 60:
                raise ValueError
        except Exception:
            await update.effective_chat.send_message("Please send a valid positive integer number of minutes (1-1440).")
            return
        storage.state.interval_seconds = minutes * 60
        await storage.save()
        # Reschedule job
        reschedule_broadcast_job(context.application, storage.state.interval_seconds)
        context.user_data.pop("awaiting", None)
        await update.effective_chat.send_message(f"Interval updated to {minutes} minutes and rescheduled.")
        await show_owner_menu(update, context)
    elif awaiting == "send_now_custom":
        if not text:
            await update.effective_chat.send_message("Message cannot be empty. Please send again.")
            return
        # Broadcast the provided custom text now
        count = 0
        for chat_id in list(storage.state.groups.keys()):
            await safe_send(context, int(chat_id), text)
            count += 1
            await asyncio.sleep(0.05)
        context.user_data.pop("awaiting", None)
        await update.effective_chat.send_message(f"Custom broadcast sent to {count} groups.")
        await show_owner_menu(update, context)


async def set_regular(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.bot_data["config"]
    storage: Storage = context.bot_data["storage"]
    if not is_owner(update.effective_user.id, cfg):
        return
    if not context.args:
        await update.effective_chat.send_message("Usage: /set_regular <text>")
        return
    text = update.effective_message.text.partition(" ")[2].strip()
    if not text:
        await update.effective_chat.send_message("Please provide the message text.")
        return
    storage.state.regular_message = text
    await storage.save()
    await update.effective_chat.send_message("Regular broadcast message updated.")


async def send_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.bot_data["config"]
    storage: Storage = context.bot_data["storage"]
    if not is_owner(update.effective_user.id, cfg):
        return
    if not context.args:
        await update.effective_chat.send_message("Usage: /send_broadcast <text>")
        return
    text = update.effective_message.text.partition(" ")[2].strip()
    if not text:
        await update.effective_chat.send_message("Please provide the message text.")
        return
    # Broadcast now
    count = 0
    for chat_id in list(storage.state.groups.keys()):
        await safe_send(context, int(chat_id), text)
        count += 1
        await asyncio.sleep(0.05)  # tiny spacing to be nice to API
    await update.effective_chat.send_message(f"Broadcast sent to {count} groups.")


async def send_broadcast_now(context: ContextTypes.DEFAULT_TYPE) -> int:
    storage: Storage = context.bot_data["storage"]
    text = storage.state.regular_message
    count = 0
    for chat_id in list(storage.state.groups.keys()):
        await safe_send(context, int(chat_id), text)
        count += 1
        await asyncio.sleep(0.05)
    return count


async def toggle_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.bot_data["config"]
    storage: Storage = context.bot_data["storage"]
    if not is_owner(update.effective_user.id, cfg):
        return
    if not context.args or context.args[0].lower() not in ("on", "off"):
        await update.effective_chat.send_message("Usage: /toggle_broadcast on|off")
        return
    on = context.args[0].lower() == "on"
    storage.state.broadcasts_enabled = on
    await storage.save()
    await update.effective_chat.send_message(
        f"Periodic broadcasts are now {'enabled' if on else 'disabled'}."
    )


async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Ignore unknown commands silently
    return


# --------------- Jobs ---------------


async def periodic_broadcast(context: CallbackContext) -> None:
    storage: Storage = context.bot_data["storage"]
    if not storage.state.broadcasts_enabled:
        return
    text = storage.state.regular_message
    for chat_id in list(storage.state.groups.keys()):
        await safe_send(context, int(chat_id), text)
        await asyncio.sleep(0.05)


def reschedule_broadcast_job(app: Application, interval_seconds: int) -> None:
    # Cancel previous job if present
    job = app.bot_data.get("broadcast_job")
    if job is not None:
        try:
            job.schedule_removal()
        except Exception:
            pass
    # Schedule new job
    new_job = app.job_queue.run_repeating(
        periodic_broadcast, interval=interval_seconds, first=60
    )
    app.bot_data["broadcast_job"] = new_job


# --------------- App Bootstrap ---------------


def build_application(config: Config, storage: Storage) -> Application:
    app = (
        ApplicationBuilder()
        .token(config.bot_token)
        .post_init(post_init)
        .build()
    )
    # Share config and storage
    app.bot_data["config"] = config
    app.bot_data["storage"] = storage

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("list_groups", list_groups))
    app.add_handler(CommandHandler("set_regular", set_regular))
    app.add_handler(CommandHandler("send_broadcast", send_broadcast))
    app.add_handler(CommandHandler("toggle_broadcast", toggle_broadcast))

    # Track when the bot is added/removed to/from a group
    app.add_handler(ChatMemberHandler(on_bot_member_update, ChatMemberHandler.MY_CHAT_MEMBER))

    # Owner inline menu callbacks
    app.add_handler(CallbackQueryHandler(handle_owner_callback, pattern=r"^owner:"))

    # Owner text input for edit message / change interval
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, owner_text_input))

    # Unknown commands handler (lowest priority)
    app.add_handler(MessageHandler(filters.COMMAND, unknown))

    # Job: periodic broadcast (use state interval)
    reschedule_broadcast_job(app, storage.state.interval_seconds)

    return app


async def post_init(app: Application) -> None:
    # Ensure bot can receive my_chat_member updates
    try:
        await app.bot.set_my_commands(
            [
                ("start", "Show help / owner commands"),
                ("menu", "Owner: open control panel"),
                ("send_broadcast", "Owner: send a custom broadcast now"),
                ("set_regular", "Owner: set the regular broadcast message"),
                ("toggle_broadcast", "Owner: enable/disable periodic broadcasts"),
                ("list_groups", "Owner: list tracked groups"),
            ]
        )
    except Exception as e:
        logger.warning("Failed to set bot commands: %s", e)


def main():
    cfg = ensure_config()
    storage = Storage(STATE_PATH)
    # Create and set an event loop BEFORE building the application so that
    # APScheduler (used by PTB's JobQueue) can find a current loop.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    # Load state using this loop
    loop.run_until_complete(storage.load())

    app = build_application(cfg, storage)
    logger.info("Bot starting...")

    try:
        app.run_polling(
            allowed_updates=["message", "my_chat_member", "chat_member", "callback_query"],
            close_loop=False,
        )
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")


if __name__ == "__main__":
    main()


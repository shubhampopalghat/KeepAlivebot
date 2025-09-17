# Telegram Group Activity Bot

This bot, when added to any group, will periodically send a broadcast message every 7 minutes to keep groups active. It also supports an owner menu to send custom broadcasts and edit the regular broadcast message.

## Features
- Regular broadcast to all tracked groups every 7 minutes
- Automatically tracks groups when the bot is added/removed
- Owner-only commands:
  - `/send_broadcast <text>`: Send a custom broadcast to all groups immediately
  - `/set_regular <text>`: Change the regular broadcast message
  - `/toggle_broadcast on|off`: Enable or disable the periodic broadcasts
  - `/list_groups`: Show tracked groups

## Files
- `main.py`: Bot source code
- `config.json`: Store your bot token and owner user ID(s)
- `state.json`: Persist groups, the regular message, and broadcast toggle (auto-created)
- `requirements.txt`: Python dependencies

## Setup (Windows)
1. Install Python 3.10+ from https://python.org
2. Open a terminal in this project folder.
3. Create and activate a virtual environment:
   ```powershell
   py -m venv .venv
   .venv\Scripts\Activate.ps1
   ```
4. Install dependencies:
   ```powershell
   pip install -r requirements.txt
   ```
5. Configure your bot in `config.json`:
   - Set `bot_token` to your BotFather token
   - Put your numeric Telegram user ID in `owner_ids` (array). You can add multiple owner IDs.

## Run
```powershell
python main.py
```
The first run will create `state.json` if it doesn't exist.

## Invite to Groups
- Add the bot to any group (and optionally promote it if required by your group settings). The bot will automatically track the group and include it in broadcasts.

## Notes
- Broadcast interval is set to 7 minutes by default in `main.py` (constant `BROADCAST_INTERVAL_SECONDS`).
- The regular broadcast message is stored in `state.json` and can be updated via `/set_regular`.
- Ensure the bot has permission to send messages in the group.

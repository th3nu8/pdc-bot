# VP Tracker Discord Bot

Tracks per-user "Valor Points" (VP) in a local SQLite database, with slash commands
to give, take, set, check, and view a leaderboard/history of VP.

## Files

- `bot.py` — main bot, defines all slash commands
- `db.py` — SQLite storage layer (creates `vp_data.db` automatically on first run)
- `requirements.txt` — Python dependencies
- `.env.example` — template for your secrets/config (copy to `.env`)
- `vp-bot.service` — systemd unit to keep the bot running as a background service

## 1. Create the Discord bot application

1. Go to https://discord.com/developers/applications and click **New Application**.
2. Go to the **Bot** tab → **Add Bot**.
3. Under **Privileged Gateway Intents**, enable **Server Members Intent** (required so the bot can resolve usernames).
4. Click **Reset Token** and copy the token — you'll put this in `.env`.
5. Go to **OAuth2 → URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot Permissions: `Send Messages`, `Embed Links`, `Use Slash Commands`, `View Channels`
6. Open the generated URL and invite the bot to your server.

## 2. Set up the project on your Linux server

```bash
# Get the files onto your server (scp, git, or just recreate them), then:
cd ~/vp-bot

# Create an isolated Python environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure secrets
cp .env.example .env
nano .env    # paste in DISCORD_TOKEN, and optionally GUILD_ID / VP_ADMIN_ROLE_ID
```

## 3. Run it (test mode)

```bash
source venv/bin/activate
python3 bot.py
```

If it prints `Logged in as YourBot#1234 | synced N commands`, it worked. Go to
your Discord server and type `/` to see the commands.

Tip: set `GUILD_ID` in `.env` while testing — global command sync can take up to
an hour to appear, but guild-specific sync is instant.

## 4. Run it permanently with systemd

```bash
# Edit vp-bot.service and replace YOUR_LINUX_USERNAME with your actual username
nano vp-bot.service

sudo cp vp-bot.service /etc/systemd/system/vp-bot.service
sudo systemctl daemon-reload
sudo systemctl enable vp-bot
sudo systemctl start vp-bot

# Check it's running
sudo systemctl status vp-bot

# View logs
journalctl -u vp-bot -f
```

The bot will now start on boot and restart automatically if it crashes.

## Commands

| Command | Who can use it | Description |
|---|---|---|
| `/give <user> <amount> <reason>` | Administrators, or a role listed in `VP_ADMIN_ROLE_ID` | Adds VP to a user and logs the reason |
| `/take <user> <amount> <reason>` | Same as above | Subtracts VP from a user and logs the reason |
| `/setvp <user> <amount> <reason>` | Same as above | Sets a user's VP to an exact value |
| `/vp [user]` | Everyone | Shows your VP, or another user's if specified |
| `/leaderboard [limit]` | Everyone | Shows the top VP holders (default 10, max 25) |
| `/vphistory [user] [limit]` | Everyone | Shows recent VP transactions, optionally filtered by user |

## Data storage

All data lives in `vp_data.db` (SQLite) in the bot's working directory — no
external database needed. Two tables:

- `users`: current VP total per Discord user ID
- `transactions`: full audit log of every give/take/set, with reason and which admin did it

Back it up by simply copying `vp_data.db`.

## Updating admin permissions later

Anyone with the Discord **Administrator** permission can always use `/give`,
`/take`, and `/setvp`. If you want a specific non-admin role (e.g. "Officer")
to manage VP too, set `VP_ADMIN_ROLE_ID` in `.env` to that role's ID and restart
the bot (`sudo systemctl restart vp-bot`).

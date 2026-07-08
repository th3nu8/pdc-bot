# VP Tracker Discord Bot

Tracks per-user "Valor Points" (VP) in a local SQLite database, with slash commands
to give, take, set, check, and view a leaderboard/history of VP.

## Files

- `bot.py` — main bot, defines all slash commands
- `db.py` — SQLite storage layer (creates `vp_data.db` automatically on first run)
- `awards_config.py` — reads `awards.json` (no restart needed when you edit it)
- `awards.json` — your list of configured awards and their role IDs (edit this to add/change awards)
- `events_config.py` — reads `events.json` and `clearance.json` (no restart needed when you edit them)
- `events.json` — your list of event types and the clearance level required to create each one
- `clearance.json` — the clearance hierarchy, lowest to highest, with a role ID per level
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
| `/award <user> <award_name> <reason>` | Administrators, or `VP_ADMIN_ROLE_ID` | Gives a user a configured award, assigning its Discord role |
| `/awards [user]` | Everyone | Lists all awards a user has received (defaults to yourself) |
| `/remove <user> <award_name>` | Administrators, or `VP_ADMIN_ROLE_ID` | Removes a user's most recent instance of an award; removes the role too if no instances remain |
| `/event <event_type> [details]` | Whoever holds the required clearance level for that event type (or higher) | Opens a private time-picker form, then posts an event announcement with ✅🟨❌ RSVP reactions |
| `/testmonthlycheck` | Administrators, or `VP_ADMIN_ROLE_ID` | Manually runs the monthly low-VP check (previous month, or current month so far) |
| `/testactivitycheck` | Administrators, or `VP_ADMIN_ROLE_ID` | Manually posts a new monthly activity check right now |
| `/finalizeactivitycheck` | Administrators, or `VP_ADMIN_ROLE_ID` | Manually finalizes the latest activity check and sends the non-reactor DM right now |

## Setting up events and clearance levels

**`clearance.json`** defines your clearance levels, numbered 1 (lowest) to however high you want:

```json
[
  { "level": 1, "name": "Recruit", "role_id": "111111111111111111" },
  { "level": 2, "name": "Member", "role_id": "222222222222222222" },
  { "level": 3, "name": "Officer", "role_id": "333333333333333333" },
  { "level": 4, "name": "Command", "role_id": "444444444444444444" }
]
```

A member's clearance is the *highest* `level` number among the roles they hold. Higher levels can do everything lower levels can — someone with the Level 4 (Command) role can create a Level 1 (Recruit) event without needing the Recruit role itself. You can add more levels (5, 6, ...) if you need finer granularity.

**`events.json`** defines your event types and which numeric clearance level each one requires:

```json
[
  { "name": "Training Exercise", "clearance": 1 },
  { "name": "Officer Meeting", "clearance": 3 }
]
```

- `name` — what shows up in `/event`'s autocomplete for the `event_type` option
- `clearance` — the numeric level required, matching a `level` from `clearance.json`

Both files are re-read on every `/event` call, so you can edit them any time without restarting the bot.

### Using /event

Run `/event event_type:"Officer Meeting" details:"Bring your reports"` — the bot checks your clearance first, then opens a **private popup form** (only you can see it) with three fields:

- **Date** — `MM/DD/YYYY`
- **Time** — 24-hour `HH:MM`
- **Your timezone** — an IANA name, pre-filled with `America/Chicago` but editable (e.g. `America/New_York`, `America/Los_Angeles`, `Europe/London`, `UTC`)

Fill it in using *your own* local time and timezone, hit submit, and the bot converts it to a Discord timestamp and posts the public announcement. Since Discord timestamps encode an exact moment, everyone who sees the announcement gets it shown in *their* own local time automatically — no server-side timezone config needed.

- The bot checks the user's clearance level before even opening the form; if it's too low, the command is rejected with an ephemeral error naming the required level
- The posted announcement gets ✅ (attending), 🟨 (maybe), and ❌ (not attending) reactions added automatically for RSVPs

## Monthly activity check

On the 1st of each month, the bot posts:

```
@Role1 @Role2
# Monthly Activity Check
React with a ✅ by <date/time, ACTIVITY_CHECK_DAYS days out>
```

in `ACTIVITY_CHECK_CHANNEL_ID`, pinging every role in `ACTIVITY_CHECK_PING_ROLE_IDS`, and reacts with ✅ itself so members have something to click. The date shown is a live Discord timestamp — it displays in each viewer's own timezone automatically.

After `ACTIVITY_CHECK_DAYS` days (default 14) have passed, the bot checks who reacted and DMs `ACTIVITY_CHECK_DM_USER_ID` the list of everyone who didn't — skipping:
- anyone with `ACTIVITY_CHECK_EXEMPT_ROLE_ID`
- anyone who doesn't hold at least one of the roles in `ACTIVITY_CHECK_PING_ROLE_IDS` (if that's set)

Use `/testactivitycheck` to post one immediately without waiting for the 1st, and `/finalizeactivitycheck` to force the follow-up DM right away instead of waiting the full `ACTIVITY_CHECK_DAYS` — handy for confirming the DM format and non-reactor logic work as expected.


## Setting up awards

Edit `awards.json` to define what awards exist:

```json
[
  {
    "name": "MVP of the Month",
    "role_id": "123456789012345678",
    "repeatable": false,
    "vp": 10
  },
  {
    "name": "Top Recruiter",
    "role_id": "123456789012345678",
    "repeatable": true,
    "vp": 5
  }
]
```

- `name` — what admins type into `/award` and `/remove` (autocomplete will suggest it)
- `role_id` — the Discord role ID to assign when this award is given (right-click the role with Developer Mode on → Copy Role ID)
- `repeatable` — `true` if a user can receive this award more than once (e.g. "Community Helper" each month); `false` if it can only be given once per user (e.g. a one-time milestone)
- `vp` — how much VP to add when this award is given, and subtract when it's removed. Set to `0` (or omit it) for an award that's purely cosmetic with no VP attached.

You can add, remove, or edit entries in `awards.json` at any time — it's re-read on every `/award`, `/awards`, and `/remove` call, so no bot restart is needed.

Note: for non-repeatable awards, giving it again while the user already has it will be blocked. For repeatable awards, each `/award` call adds a new entry to their award history even if the Discord role is already assigned (Discord roles can't "stack," but the VP bot's own award log keeps every instance).


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

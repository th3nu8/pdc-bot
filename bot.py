import os
import datetime
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

import db
import awards_config

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")  # optional: set for instant command sync during testing
ADMIN_ROLE_ID = os.getenv("VP_ADMIN_ROLE_ID")  # optional: role allowed to manage VP besides Administrators
LOG_CHANNEL_ID = os.getenv("LOG_CHANNEL_ID")  # optional: channel that receives a copy of every give/take/setvp
MONTHLY_ALERT_CHANNEL_ID = os.getenv("MONTHLY_ALERT_CHANNEL_ID")  # channel for the low-VP monthly notice
MONTHLY_ALERT_ROLE_ID = os.getenv("MONTHLY_ALERT_ROLE_ID")  # role to ping in that notice
MONTHLY_CHECK_ROLE_ID = os.getenv("MONTHLY_CHECK_ROLE_ID")  # if set, only members with this role are checked at all
MONTHLY_CHECK_EXEMPT_ROLE_ID = os.getenv("MONTHLY_CHECK_EXEMPT_ROLE_ID")  # members with this role skip the monthly check entirely
MIN_MONTHLY_VP = int(os.getenv("MIN_MONTHLY_VP", "4"))  # threshold for the monthly check

intents = discord.Intents.default()
intents.members = True  # needed to resolve member display names

bot = commands.Bot(command_prefix="!", intents=intents)


def is_vp_admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True
        if ADMIN_ROLE_ID:
            role = interaction.guild.get_role(int(ADMIN_ROLE_ID))
            if role and role in interaction.user.roles:
                return True
        return False
    return app_commands.check(predicate)


async def post_log(embed: discord.Embed):
    """Sends a copy of a VP change embed to the configured log channel, if set."""
    if not LOG_CHANNEL_ID:
        return
    channel = bot.get_channel(int(LOG_CHANNEL_ID))
    if channel is None:
        try:
            channel = await bot.fetch_channel(int(LOG_CHANNEL_ID))
        except discord.HTTPException:
            channel = None
    if channel is not None:
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            print(f"Missing permission to send messages in log channel {LOG_CHANNEL_ID}")


@bot.event
async def on_ready():
    db.init_db()
    if GUILD_ID:
        guild_obj = discord.Object(id=int(GUILD_ID))
        bot.tree.copy_global_to(guild=guild_obj)
        synced = await bot.tree.sync(guild=guild_obj)
    else:
        synced = await bot.tree.sync()
    if not monthly_vp_check.is_running():
        monthly_vp_check.start()
    print(f"Logged in as {bot.user} | synced {len(synced)} commands")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message(
            "You don't have permission to manage VP.", ephemeral=True
        )
    else:
        if interaction.response.is_done():
            await interaction.followup.send(f"Error: {error}", ephemeral=True)
        else:
            await interaction.response.send_message(f"Error: {error}", ephemeral=True)
        raise error


# ---------- /give ----------
@bot.tree.command(name="give", description="Give Valor Points to a user")
@app_commands.describe(user="User to give VP to", amount="Amount of VP to give (must be positive)", reason="Reason for giving VP")
@is_vp_admin()
async def give(interaction: discord.Interaction, user: discord.Member, amount: int, reason: str):
    if amount <= 0:
        await interaction.response.send_message("Amount must be positive. Use /take to remove VP.", ephemeral=True)
        return
    new_total = db.add_vp(user.id, str(user), amount, reason, interaction.user.id)
    embed = discord.Embed(title="VP Awarded", color=discord.Color.green())
    embed.add_field(name="User", value=user.mention, inline=True)
    embed.add_field(name="Amount", value=f"+{amount}", inline=True)
    embed.add_field(name="New Total", value=str(new_total), inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_footer(text=f"Awarded by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)
    await post_log(embed)


# ---------- /take ----------
@bot.tree.command(name="take", description="Remove Valor Points from a user")
@app_commands.describe(user="User to remove VP from", amount="Amount of VP to remove (must be positive)", reason="Reason for removing VP")
@is_vp_admin()
async def take(interaction: discord.Interaction, user: discord.Member, amount: int, reason: str):
    if amount <= 0:
        await interaction.response.send_message("Amount must be positive.", ephemeral=True)
        return
    new_total = db.add_vp(user.id, str(user), -amount, reason, interaction.user.id)
    embed = discord.Embed(title="VP Removed", color=discord.Color.red())
    embed.add_field(name="User", value=user.mention, inline=True)
    embed.add_field(name="Amount", value=f"-{amount}", inline=True)
    embed.add_field(name="New Total", value=str(new_total), inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_footer(text=f"Removed by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)
    await post_log(embed)


# ---------- /setvp ----------
@bot.tree.command(name="setvp", description="Set a user's VP to an exact value")
@app_commands.describe(user="User to set VP for", amount="Exact VP value to set", reason="Reason for the adjustment")
@is_vp_admin()
async def setvp(interaction: discord.Interaction, user: discord.Member, amount: int, reason: str):
    if amount < 0:
        await interaction.response.send_message("VP cannot be set below 0.", ephemeral=True)
        return
    new_total = db.set_vp(user.id, str(user), amount, reason, interaction.user.id)
    embed = discord.Embed(title="VP Set", color=discord.Color.blurple())
    embed.add_field(name="User", value=user.mention, inline=True)
    embed.add_field(name="New Total", value=str(new_total), inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_footer(text=f"Set by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)
    await post_log(embed)


# ---------- /vp ----------
@bot.tree.command(name="vp", description="Check your or another user's VP balance")
@app_commands.describe(user="User to check (defaults to yourself)")
async def vp(interaction: discord.Interaction, user: discord.Member = None):
    target = user or interaction.user
    total = db.get_vp(target.id, str(target))
    embed = discord.Embed(title="Valor Points", color=discord.Color.gold())
    embed.add_field(name="User", value=target.mention, inline=True)
    embed.add_field(name="VP", value=str(total), inline=True)
    await interaction.response.send_message(embed=embed)


# ---------- /leaderboard ----------
@bot.tree.command(name="leaderboard", description="Show the top VP holders")
@app_commands.describe(limit="How many users to show (default 10, max 25)")
async def leaderboard(interaction: discord.Interaction, limit: int = 10):
    limit = max(1, min(limit, 25))
    rows = db.get_leaderboard(limit)
    if not rows:
        await interaction.response.send_message("No VP data yet.", ephemeral=True)
        return
    lines = []
    for i, (user_id, username, points) in enumerate(rows, start=1):
        lines.append(f"**{i}.** <@{user_id}> — {points} VP")
    embed = discord.Embed(title="Valor Points Leaderboard", description="\n".join(lines), color=discord.Color.purple())
    await interaction.response.send_message(embed=embed)


# ---------- /vphistory ----------
@bot.tree.command(name="vphistory", description="Show recent VP transactions")
@app_commands.describe(user="Filter by a specific user (optional)", limit="How many entries to show (default 10, max 25)")
async def vphistory(interaction: discord.Interaction, user: discord.Member = None, limit: int = 10):
    limit = max(1, min(limit, 25))
    rows = db.get_history(user.id if user else None, limit)
    if not rows:
        await interaction.response.send_message("No transactions found.", ephemeral=True)
        return
    lines = []
    for user_id, amount, reason, admin_id, timestamp in rows:
        sign = "+" if amount >= 0 else ""
        admin_str = f"<@{admin_id}>" if admin_id else "unknown"
        lines.append(f"<@{user_id}>: {sign}{amount} VP — {reason} (by {admin_str})")
    embed = discord.Embed(title="VP Transaction History", description="\n".join(lines), color=discord.Color.teal())
    await interaction.response.send_message(embed=embed)


async def award_name_autocomplete(interaction: discord.Interaction, current: str):
    names = awards_config.award_names()
    matches = [n for n in names if current.lower() in n.lower()]
    return [app_commands.Choice(name=n, value=n) for n in matches[:25]]


# ---------- /award ----------
@bot.tree.command(name="award", description="Give a user a configured award (assigns its role)")
@app_commands.describe(user="User to award", award_name="Name of the award (see awards.json)", reason="Reason for the award")
@app_commands.autocomplete(award_name=award_name_autocomplete)
@is_vp_admin()
async def award(interaction: discord.Interaction, user: discord.Member, award_name: str, reason: str):
    entry = awards_config.find_award(award_name)
    if not entry:
        await interaction.response.send_message(
            f"Unknown award '{award_name}'. Check awards.json for valid names.", ephemeral=True
        )
        return

    canonical_name = entry["name"]
    repeatable = bool(entry.get("repeatable", False))

    if not repeatable and db.has_award(user.id, canonical_name):
        await interaction.response.send_message(
            f"{user.display_name} already has the **{canonical_name}** award, and it isn't repeatable.",
            ephemeral=True,
        )
        return

    role_id = entry.get("role_id")
    if role_id and role_id != "PUT_ROLE_ID_HERE":
        role = interaction.guild.get_role(int(role_id))
        if role and role not in user.roles:
            try:
                await user.add_roles(role, reason=f"Award: {canonical_name} — {reason}")
            except discord.Forbidden:
                await interaction.response.send_message(
                    "I don't have permission to assign that role. Check the bot's role position and permissions.",
                    ephemeral=True,
                )
                return

    db.add_award(user.id, str(user), canonical_name, reason, interaction.user.id)

    embed = discord.Embed(title="Award Given", color=discord.Color.gold())
    embed.add_field(name="User", value=user.mention, inline=True)
    embed.add_field(name="Award", value=canonical_name, inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_footer(text=f"Awarded by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)
    await post_log(embed)


# ---------- /awards ----------
@bot.tree.command(name="awards", description="Show all awards a user has received")
@app_commands.describe(user="User to check (defaults to yourself)")
async def awards_cmd(interaction: discord.Interaction, user: discord.Member = None):
    target = user or interaction.user
    rows = db.get_awards(target.id)
    if not rows:
        await interaction.response.send_message(f"{target.display_name} has no awards yet.", ephemeral=True)
        return

    lines = []
    for award_name, reason, admin_id, timestamp in rows:
        admin_str = f"<@{admin_id}>" if admin_id else "unknown"
        try:
            date_str = datetime.datetime.fromisoformat(timestamp).strftime("%Y-%m-%d")
        except ValueError:
            date_str = timestamp
        lines.append(f"**{award_name}** — {reason}\n*by {admin_str} on {date_str}*")

    embed = discord.Embed(
        title=f"{target.display_name}'s Awards",
        description="\n\n".join(lines),
        color=discord.Color.gold(),
    )
    await interaction.response.send_message(embed=embed)


# ---------- /remove (award removal) ----------
@bot.tree.command(name="remove", description="Remove an award from a user")
@app_commands.describe(user="User to remove the award from", award_name="Name of the award to remove")
@app_commands.autocomplete(award_name=award_name_autocomplete)
@is_vp_admin()
async def remove_award(interaction: discord.Interaction, user: discord.Member, award_name: str):
    entry = awards_config.find_award(award_name)
    if not entry:
        await interaction.response.send_message(f"Unknown award '{award_name}'.", ephemeral=True)
        return

    canonical_name = entry["name"]
    removed = db.remove_last_award(user.id, canonical_name)
    if not removed:
        await interaction.response.send_message(
            f"{user.display_name} doesn't have the **{canonical_name}** award.", ephemeral=True
        )
        return

    remaining = db.count_award(user.id, canonical_name)
    role_id = entry.get("role_id")
    if remaining == 0 and role_id and role_id != "PUT_ROLE_ID_HERE":
        role = interaction.guild.get_role(int(role_id))
        if role and role in user.roles:
            try:
                await user.remove_roles(role, reason=f"Award removed: {canonical_name}")
            except discord.Forbidden:
                pass

    embed = discord.Embed(title="Award Removed", color=discord.Color.red())
    embed.add_field(name="User", value=user.mention, inline=True)
    embed.add_field(name="Award", value=canonical_name, inline=True)
    if remaining > 0:
        embed.add_field(name="Remaining instances", value=str(remaining), inline=True)
    embed.set_footer(text=f"Removed by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)
    await post_log(embed)


def _previous_month_range(now: datetime.datetime):
    """Returns (start, end) datetimes in UTC covering the full previous calendar month."""
    first_of_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if first_of_this_month.month == 1:
        prev_start = first_of_this_month.replace(year=first_of_this_month.year - 1, month=12)
    else:
        prev_start = first_of_this_month.replace(month=first_of_this_month.month - 1)
    return prev_start, first_of_this_month


async def _run_monthly_check(month_key: str, prev_start: datetime.datetime, prev_end: datetime.datetime, channel: discord.abc.Messageable):
    if not GUILD_ID:
        print("MONTHLY_ALERT skipped: GUILD_ID is not set.")
        return
    guild = bot.get_guild(int(GUILD_ID))
    if guild is None:
        print("MONTHLY_ALERT skipped: bot is not in the configured GUILD_ID.")
        return

    low_vp = []
    for member in guild.members:
        if member.bot:
            continue
        if MONTHLY_CHECK_ROLE_ID and not any(str(r.id) == MONTHLY_CHECK_ROLE_ID for r in member.roles):
            continue
        if MONTHLY_CHECK_EXEMPT_ROLE_ID and any(str(r.id) == MONTHLY_CHECK_EXEMPT_ROLE_ID for r in member.roles):
            continue
        earned = db.get_vp_earned_in_range(member.id, prev_start.isoformat(), prev_end.isoformat())
        if earned < MIN_MONTHLY_VP:
            low_vp.append((member, earned))

    if not low_vp:
        await channel.send(f"Monthly VP check for **{prev_start.strftime('%B %Y')}**: everyone met the {MIN_MONTHLY_VP} VP minimum. 🎉")
        return

    role_mention = f"<@&{MONTHLY_ALERT_ROLE_ID}>" if MONTHLY_ALERT_ROLE_ID else ""
    lines = [f"{member.mention} — {earned} VP" for member, earned in low_vp]
    embed = discord.Embed(
        title=f"Monthly VP Check — {prev_start.strftime('%B %Y')}",
        description=f"These members earned fewer than **{MIN_MONTHLY_VP} VP** last month:\n\n" + "\n".join(lines),
        color=discord.Color.orange(),
    )
    await channel.send(
        content=role_mention,
        embed=embed,
        allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False),
    )


@tasks.loop(time=datetime.time(hour=9, tzinfo=datetime.timezone.utc))
async def monthly_vp_check():
    now = datetime.datetime.now(datetime.timezone.utc)
    if now.day != 1:
        return
    prev_start, prev_end = _previous_month_range(now)
    month_key = prev_start.strftime("%Y-%m")
    if db.has_monthly_check_run(month_key):
        return
    db.mark_monthly_check_run(month_key)

    if not MONTHLY_ALERT_CHANNEL_ID:
        print("MONTHLY_ALERT skipped: MONTHLY_ALERT_CHANNEL_ID is not set.")
        return
    channel = bot.get_channel(int(MONTHLY_ALERT_CHANNEL_ID))
    if channel is None:
        try:
            channel = await bot.fetch_channel(int(MONTHLY_ALERT_CHANNEL_ID))
        except discord.HTTPException:
            print(f"MONTHLY_ALERT skipped: could not access channel {MONTHLY_ALERT_CHANNEL_ID}.")
            return

    await _run_monthly_check(month_key, prev_start, prev_end, channel)


@monthly_vp_check.before_loop
async def before_monthly_vp_check():
    await bot.wait_until_ready()


# ---------- /testmonthlycheck ----------
@bot.tree.command(name="testmonthlycheck", description="Manually run the low-VP check right now (for testing)")
@app_commands.describe(period="Which period to check: last full month (real behavior) or current month so far (for testing)")
@app_commands.choices(period=[
    app_commands.Choice(name="Previous month (real monthly behavior)", value="previous"),
    app_commands.Choice(name="Current month so far (for testing)", value="current"),
])
@is_vp_admin()
async def testmonthlycheck(interaction: discord.Interaction, period: app_commands.Choice[str] = None):
    await interaction.response.defer(ephemeral=True)
    if not MONTHLY_ALERT_CHANNEL_ID:
        await interaction.followup.send("MONTHLY_ALERT_CHANNEL_ID is not set in .env.", ephemeral=True)
        return
    channel = bot.get_channel(int(MONTHLY_ALERT_CHANNEL_ID))
    if channel is None:
        try:
            channel = await bot.fetch_channel(int(MONTHLY_ALERT_CHANNEL_ID))
        except discord.HTTPException:
            await interaction.followup.send("Could not access MONTHLY_ALERT_CHANNEL_ID.", ephemeral=True)
            return
    now = datetime.datetime.now(datetime.timezone.utc)
    use_current = period is not None and period.value == "current"
    if use_current:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = now
    else:
        start, end = _previous_month_range(now)
    await _run_monthly_check("manual-test", start, end, channel)
    await interaction.followup.send(f"Ran the check ({'current month so far' if use_current else 'previous month'}) and posted results in <#{MONTHLY_ALERT_CHANNEL_ID}>.", ephemeral=True)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in.")
    bot.run(TOKEN)

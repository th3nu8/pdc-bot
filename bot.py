import os
import re
import datetime
from zoneinfo import ZoneInfo
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

import db
import awards_config
import events_config
import monitor_config
import detachments_config

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

ACTIVITY_CHECK_CHANNEL_ID = os.getenv("ACTIVITY_CHECK_CHANNEL_ID")  # channel where the monthly activity check is posted
ACTIVITY_CHECK_PING_ROLE_IDS = [r.strip() for r in os.getenv("ACTIVITY_CHECK_PING_ROLE_IDS", "").split(",") if r.strip()]
ACTIVITY_CHECK_EXEMPT_ROLE_ID = os.getenv("ACTIVITY_CHECK_EXEMPT_ROLE_ID")  # members with this role are never reported as non-reactors
ACTIVITY_CHECK_DM_USER_ID = os.getenv("ACTIVITY_CHECK_DM_USER_ID")  # user who receives the non-reactor DM summary
ACTIVITY_CHECK_DAYS = int(os.getenv("ACTIVITY_CHECK_DAYS", "14"))  # how many days members have to react

EVENT_PING_ROLE_ID = os.getenv("EVENT_PING_ROLE_ID")  # role pinged whenever /event posts a new announcement

MONITOR_CHANNEL_ID = os.getenv("MONITOR_CHANNEL_ID")  # channel where up/down alerts are posted
MONITOR_INTERVAL_MINUTES = int(os.getenv("MONITOR_INTERVAL_MINUTES", "5"))  # how often to check the sites

MEMBER_COUNT_CHANNEL_ID = os.getenv("MEMBER_COUNT_CHANNEL_ID")  # channel for join/leave + live member count

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
    if not activity_check_loop.is_running():
        activity_check_loop.start()
    if not site_monitor_loop.is_running():
        site_monitor_loop.start()
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


async def _get_member_count_channel():
    if not MEMBER_COUNT_CHANNEL_ID:
        return None
    channel = bot.get_channel(int(MEMBER_COUNT_CHANNEL_ID))
    if channel is None:
        try:
            channel = await bot.fetch_channel(int(MEMBER_COUNT_CHANNEL_ID))
        except discord.HTTPException:
            print(f"MEMBER_COUNT skipped: could not access channel {MEMBER_COUNT_CHANNEL_ID}.")
            return None
    return channel


@bot.event
async def on_member_join(member: discord.Member):
    channel = await _get_member_count_channel()
    if channel is None:
        return
    embed = discord.Embed(
        title="📥 Member Joined",
        description=f"{member.mention} just joined the server!",
        color=discord.Color.green(),
    )
    embed.add_field(name="Member Count", value=str(member.guild.member_count))
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
    try:
        await channel.send(embed=embed)
    except discord.Forbidden:
        print(f"MEMBER_COUNT: missing permission to send in channel {MEMBER_COUNT_CHANNEL_ID}")


@bot.event
async def on_member_remove(member: discord.Member):
    channel = await _get_member_count_channel()
    if channel is None:
        return
    embed = discord.Embed(
        title="📤 Member Left",
        description=f"{member} left the server.",
        color=discord.Color.red(),
    )
    embed.add_field(name="Member Count", value=str(member.guild.member_count))
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
    try:
        await channel.send(embed=embed)
    except discord.Forbidden:
        print(f"MEMBER_COUNT: missing permission to send in channel {MEMBER_COUNT_CHANNEL_ID}")


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


def _extract_member_ids(text: str) -> set:
    """Pulls Discord snowflake IDs out of a string of @mentions and/or raw IDs."""
    return {int(match) for match in re.findall(r"\d{15,20}", text or "")}


def _resolve_bulk_targets(interaction: discord.Interaction, role: discord.Role, users: str) -> set:
    targets = set()
    if role:
        targets.update(m for m in role.members if not m.bot)
    if users:
        for uid in _extract_member_ids(users):
            member = interaction.guild.get_member(uid)
            if member and not member.bot:
                targets.add(member)
    return targets


def _bulk_summary_embed(title: str, amount: int, reason: str, results: list, color) -> discord.Embed:
    sign = "+" if amount >= 0 else ""
    lines = [f"{m.mention}: {sign}{amount} VP → now {total}" for m, total in results]
    description = "\n".join(lines)
    if len(description) > 3900:
        description = description[:3900] + "\n… (list truncated)"
    embed = discord.Embed(
        title=title,
        description=f"**{sign}{amount} VP** to **{len(results)}** member(s)\nReason: {reason}\n\n{description}",
        color=color,
    )
    return embed


# ---------- /bulkgive ----------
@bot.tree.command(name="bulkgive", description="Give VP to a group of people at once (by role and/or a list of users)")
@app_commands.describe(
    amount="Amount of VP to give to each person (positive)",
    reason="Reason for the VP",
    role="Give to everyone with this role (optional)",
    users="Space-separated @mentions or user IDs (optional)",
)
@is_vp_admin()
async def bulkgive(interaction: discord.Interaction, amount: int, reason: str, role: discord.Role = None, users: str = None):
    if amount <= 0:
        await interaction.response.send_message("Amount must be positive. Use /bulktake to remove VP.", ephemeral=True)
        return
    if not role and not users:
        await interaction.response.send_message("Specify a role, a list of users, or both.", ephemeral=True)
        return

    targets = _resolve_bulk_targets(interaction, role, users)
    if not targets:
        await interaction.response.send_message("No valid members found from that role/list.", ephemeral=True)
        return

    await interaction.response.defer()
    results = []
    for member in targets:
        new_total = db.add_vp(member.id, str(member), amount, reason, interaction.user.id)
        results.append((member, new_total))

    embed = _bulk_summary_embed("Bulk VP Awarded", amount, reason, results, discord.Color.green())
    embed.set_footer(text=f"Awarded by {interaction.user.display_name}")
    await interaction.followup.send(embed=embed)
    await post_log(embed)


# ---------- /bulktake ----------
@bot.tree.command(name="bulktake", description="Remove VP from a group of people at once (by role and/or a list of users)")
@app_commands.describe(
    amount="Amount of VP to remove from each person (positive)",
    reason="Reason for removing VP",
    role="Take from everyone with this role (optional)",
    users="Space-separated @mentions or user IDs (optional)",
)
@is_vp_admin()
async def bulktake(interaction: discord.Interaction, amount: int, reason: str, role: discord.Role = None, users: str = None):
    if amount <= 0:
        await interaction.response.send_message("Amount must be positive.", ephemeral=True)
        return
    if not role and not users:
        await interaction.response.send_message("Specify a role, a list of users, or both.", ephemeral=True)
        return

    targets = _resolve_bulk_targets(interaction, role, users)
    if not targets:
        await interaction.response.send_message("No valid members found from that role/list.", ephemeral=True)
        return

    await interaction.response.defer()
    results = []
    for member in targets:
        new_total = db.add_vp(member.id, str(member), -amount, reason, interaction.user.id)
        results.append((member, new_total))

    embed = _bulk_summary_embed("Bulk VP Removed", -amount, reason, results, discord.Color.red())
    embed.set_footer(text=f"Removed by {interaction.user.display_name}")
    await interaction.followup.send(embed=embed)
    await post_log(embed)


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

    vp_amount = entry.get("vp", 0)
    new_total = None
    if vp_amount:
        new_total = db.add_vp(user.id, str(user), vp_amount, f"Award: {canonical_name} — {reason}", interaction.user.id)

    embed = discord.Embed(title="Award Given", color=discord.Color.gold())
    embed.add_field(name="User", value=user.mention, inline=True)
    embed.add_field(name="Award", value=canonical_name, inline=True)
    if vp_amount:
        vp_text = f"+{vp_amount}" if vp_amount > 0 else str(vp_amount)
        embed.add_field(name="VP", value=f"{vp_text} (now {new_total})", inline=True)
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

    vp_amount = entry.get("vp", 0)
    new_total = None
    if vp_amount:
        new_total = db.add_vp(user.id, str(user), -vp_amount, f"Award removed: {canonical_name}", interaction.user.id)

    embed = discord.Embed(title="Award Removed", color=discord.Color.red())
    embed.add_field(name="User", value=user.mention, inline=True)
    embed.add_field(name="Award", value=canonical_name, inline=True)
    if vp_amount:
        embed.add_field(name="VP", value=f"-{vp_amount} (now {new_total})", inline=True)
    if remaining > 0:
        embed.add_field(name="Remaining instances", value=str(remaining), inline=True)
    embed.set_footer(text=f"Removed by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)
    await post_log(embed)


def _chunk_text(text, limit=1900):
    """Splits text into chunks under Discord's 2000-char message limit, breaking on newlines."""
    lines = text.split("\n")
    chunks = []
    current = ""
    for line in lines:
        if current and len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


async def _post_activity_check(month_key: str, now: datetime.datetime):
    if not ACTIVITY_CHECK_CHANNEL_ID:
        print("ACTIVITY_CHECK skipped: ACTIVITY_CHECK_CHANNEL_ID is not set.")
        return
    channel = bot.get_channel(int(ACTIVITY_CHECK_CHANNEL_ID))
    if channel is None:
        try:
            channel = await bot.fetch_channel(int(ACTIVITY_CHECK_CHANNEL_ID))
        except discord.HTTPException:
            print(f"ACTIVITY_CHECK skipped: could not access channel {ACTIVITY_CHECK_CHANNEL_ID}.")
            return

    deadline = now + datetime.timedelta(days=ACTIVITY_CHECK_DAYS)
    deadline_ts = int(deadline.timestamp())
    role_mentions = " ".join(f"<@&{rid}>" for rid in ACTIVITY_CHECK_PING_ROLE_IDS)

    content = (
        f"{role_mentions}\n"
        f"# Monthly Activity Check\n"
        f"React with a ✅ by <t:{deadline_ts}:F> (<t:{deadline_ts}:R>)"
    ).strip()

    message = await channel.send(
        content,
        allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False),
    )
    await message.add_reaction("✅")
    db.create_activity_check(month_key, channel.id, message.id, now.isoformat(), deadline.isoformat())


async def _finalize_activity_check(check_id: int, channel_id: int, message_id: int):
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except discord.HTTPException:
            print(f"ACTIVITY_CHECK finalize skipped: could not access channel {channel_id}.")
            db.mark_activity_check_dm_sent(check_id)
            return

    try:
        message = await channel.fetch_message(message_id)
    except discord.HTTPException:
        print(f"ACTIVITY_CHECK finalize skipped: could not fetch message {message_id}.")
        db.mark_activity_check_dm_sent(check_id)
        return

    reactors = set()
    for reaction in message.reactions:
        if str(reaction.emoji) == "✅":
            async for user in reaction.users():
                if not user.bot:
                    reactors.add(user.id)

    guild = channel.guild
    non_reactors = []
    if guild is not None:
        for member in guild.members:
            if member.bot:
                continue
            if member.id in reactors:
                continue
            if ACTIVITY_CHECK_EXEMPT_ROLE_ID and any(str(r.id) == ACTIVITY_CHECK_EXEMPT_ROLE_ID for r in member.roles):
                continue
            if ACTIVITY_CHECK_PING_ROLE_IDS and not any(str(r.id) in ACTIVITY_CHECK_PING_ROLE_IDS for r in member.roles):
                continue
            non_reactors.append(member)

    db.mark_activity_check_dm_sent(check_id)

    if not ACTIVITY_CHECK_DM_USER_ID:
        print("ACTIVITY_CHECK: no ACTIVITY_CHECK_DM_USER_ID set, skipping DM.")
        return

    try:
        dm_user = await bot.fetch_user(int(ACTIVITY_CHECK_DM_USER_ID))
    except discord.HTTPException:
        print(f"ACTIVITY_CHECK: could not fetch DM target user {ACTIVITY_CHECK_DM_USER_ID}.")
        return

    if non_reactors:
        lines = [f"- {m.mention} ({m})" for m in non_reactors]
        header = f"**Monthly Activity Check — {len(non_reactors)} member(s) didn't react:**"
        for chunk in _chunk_text(header + "\n" + "\n".join(lines)):
            try:
                await dm_user.send(chunk)
            except discord.Forbidden:
                print(f"ACTIVITY_CHECK: could not DM user {ACTIVITY_CHECK_DM_USER_ID} (DMs closed?).")
                return
    else:
        try:
            await dm_user.send("Monthly Activity Check: everyone reacted! 🎉")
        except discord.Forbidden:
            print(f"ACTIVITY_CHECK: could not DM user {ACTIVITY_CHECK_DM_USER_ID} (DMs closed?).")


@tasks.loop(time=datetime.time(hour=9, tzinfo=datetime.timezone.utc))
async def activity_check_loop():
    now = datetime.datetime.now(datetime.timezone.utc)

    month_key = now.strftime("%Y-%m")
    if now.day == 1 and not db.has_activity_check_posted(month_key):
        await _post_activity_check(month_key, now)

    for check_id, channel_id, message_id in db.get_pending_activity_checks(now.isoformat()):
        await _finalize_activity_check(check_id, channel_id, message_id)


@activity_check_loop.before_loop
async def before_activity_check_loop():
    await bot.wait_until_ready()


# ---------- /testactivitycheck ----------
@bot.tree.command(name="testactivitycheck", description="Manually post a new monthly activity check right now (for testing)")
@is_vp_admin()
async def testactivitycheck(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if not ACTIVITY_CHECK_CHANNEL_ID:
        await interaction.followup.send("ACTIVITY_CHECK_CHANNEL_ID is not set in .env.", ephemeral=True)
        return
    now = datetime.datetime.now(datetime.timezone.utc)
    month_key = f"manual-{now.strftime('%Y%m%d%H%M%S')}"
    await _post_activity_check(month_key, now)
    await interaction.followup.send(f"Posted a test activity check in <#{ACTIVITY_CHECK_CHANNEL_ID}>.", ephemeral=True)


# ---------- /finalizeactivitycheck ----------
@bot.tree.command(name="finalizeactivitycheck", description="Manually finalize the most recent activity check and DM the results now (for testing)")
@is_vp_admin()
async def finalizeactivitycheck(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    row = db.get_latest_activity_check()
    if not row:
        await interaction.followup.send("No activity check has been posted yet. Run /testactivitycheck first.", ephemeral=True)
        return
    check_id, channel_id, message_id = row
    await _finalize_activity_check(check_id, channel_id, message_id)
    await interaction.followup.send("Finalized the latest activity check and sent the DM (if configured and non-empty).", ephemeral=True)


async def event_type_autocomplete(interaction: discord.Interaction, current: str):
    names = events_config.event_names()
    matches = [n for n in names if current.lower() in n.lower()]
    return [app_commands.Choice(name=n, value=n) for n in matches[:25]]


async def detachment_autocomplete(interaction: discord.Interaction, current: str):
    names = detachments_config.detachment_names()
    matches = [n for n in names if current.lower() in n.lower()]
    return [app_commands.Choice(name=n, value=n) for n in matches[:25]]


async def ping_autocomplete(interaction: discord.Interaction, current: str):
    event_type = interaction.namespace.event_type
    if not event_type:
        return []
    entry = events_config.find_event(event_type)
    if not entry:
        return []
    names = [p.get("name", "") for p in entry.get("pings", []) if p.get("name")]
    matches = [n for n in names if current.lower() in n.lower()]
    return [app_commands.Choice(name=n, value=n) for n in matches[:25]]


class EventTimeModal(discord.ui.Modal):
    """Private form (only visible to the person running /event) for picking the event's date/time/timezone."""

    TIMEZONE_OPTIONS = [
        ("Eastern Time (ET)", "America/New_York"),
        ("Central Time (CT)", "America/Chicago"),
        ("Mountain Time (MT)", "America/Denver"),
        ("Pacific Time (PT)", "America/Los_Angeles"),
        ("Alaska Time (AKT)", "America/Anchorage"),
        ("Hawaii Time (HT)", "Pacific/Honolulu"),
        ("UTC", "UTC"),
        ("London (GMT/BST)", "Europe/London"),
        ("Central Europe (CET/CEST)", "Europe/Berlin"),
        ("India (IST)", "Asia/Kolkata"),
        ("Japan (JST)", "Asia/Tokyo"),
        ("Australia Eastern (AET)", "Australia/Sydney"),
    ]

    def __init__(
        self,
        entry: dict,
        details: str,
        host: discord.Member,
        origin_channel: discord.abc.Messageable,
        primary_role_id: str = None,
        detachment: dict = None,
    ):
        super().__init__(title=f"Schedule: {entry['name'][:30]}")
        self.entry = entry
        self.details = details
        self.host = host
        self.origin_channel = origin_channel
        self.primary_role_id = primary_role_id
        self.detachment = detachment

        self.date_input = discord.ui.TextInput(
            label="Date (MM/DD/YYYY)", placeholder="07/15/2026", required=True, max_length=10
        )
        self.time_input = discord.ui.TextInput(
            label="Time — 24 hour (HH:MM)", placeholder="18:30", required=True, max_length=5
        )
        self.timezone_select = discord.ui.Select(
            placeholder="Choose your timezone",
            options=[
                discord.SelectOption(label=display_name, value=tz_name, default=(tz_name == "America/Chicago"))
                for display_name, tz_name in self.TIMEZONE_OPTIONS
            ],
        )
        self.timezone_label = discord.ui.Label(
            text="Your timezone",
            description="The event time will be shown correctly to everyone regardless of their own timezone",
            component=self.timezone_select,
        )

        self.add_item(self.date_input)
        self.add_item(self.time_input)
        self.add_item(self.timezone_label)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            naive_dt = datetime.datetime.strptime(f"{self.date_input.value} {self.time_input.value}", "%m/%d/%Y %H:%M")
        except ValueError:
            await interaction.response.send_message(
                "Couldn't parse that date/time. Use MM/DD/YYYY for the date and 24-hour HH:MM for the time "
                "(e.g. `07/15/2026` and `18:30`).",
                ephemeral=True,
            )
            return

        tz_name = self.timezone_select.values[0] if self.timezone_select.values else "America/Chicago"
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            await interaction.response.send_message(
                f"Unknown timezone '{tz_name}'. Please try again and pick one from the dropdown.",
                ephemeral=True,
            )
            return

        localized = naive_dt.replace(tzinfo=tz)
        ts = int(localized.timestamp())

        embed = discord.Embed(title=f"📅 {self.entry['name']}", color=discord.Color.blue())
        embed.add_field(name="When", value=f"<t:{ts}:t> on <t:{ts}:D> (<t:{ts}:R>)", inline=False)
        if self.details:
            embed.add_field(name="Details", value=self.details, inline=False)
        embed.add_field(name="RSVP", value="✅ Attending  🟨 Maybe  ❌ Not Attending", inline=False)
        embed.set_footer(text=f"Hosted by {self.host.display_name}")

        # Determine where the event gets posted: the detachment's channel if one was
        # picked, otherwise the same channel /event was run in.
        target_channel = self.origin_channel
        detachment_role_id = None
        if self.detachment:
            det_channel_id = self.detachment.get("channel_id")
            if det_channel_id and det_channel_id != "PUT_CHANNEL_ID_HERE":
                fetched = interaction.client.get_channel(int(det_channel_id))
                if fetched is None:
                    try:
                        fetched = await interaction.client.fetch_channel(int(det_channel_id))
                    except discord.HTTPException:
                        fetched = None
                if fetched is not None:
                    target_channel = fetched
            det_role_id = self.detachment.get("role_id")
            if det_role_id and det_role_id != "PUT_ROLE_ID_HERE":
                detachment_role_id = det_role_id

        # Two independent pings can both apply: the event type's chosen ping (posted
        # wherever the event ends up), and the detachment's own ping (only if a
        # detachment was picked). Combine and dedupe.
        role_ids = []
        if self.primary_role_id:
            role_ids.append(self.primary_role_id)
        if detachment_role_id and detachment_role_id not in role_ids:
            role_ids.append(detachment_role_id)
        if not role_ids and EVENT_PING_ROLE_ID:
            role_ids.append(EVENT_PING_ROLE_ID)
        content = " ".join(f"<@&{r}>" for r in role_ids) if role_ids else None

        try:
            message = await target_channel.send(
                content=content,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False),
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "I don't have permission to post in that detachment's channel. Check the bot's permissions there.",
                ephemeral=True,
            )
            return

        for emoji in ("✅", "🟨", "❌"):
            await message.add_reaction(emoji)

        db.create_event_message(message.id, self.origin_channel.id, self.entry["name"])

        if target_channel.id != self.origin_channel.id:
            await interaction.response.send_message(
                f"Event posted in {target_channel.mention}. You'll get a notice here whenever someone reacts to it.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message("Event posted!", ephemeral=True)


EVENT_RSVP_LABELS = {"✅": "✅ Attending", "🟨": "🟨 Maybe", "❌": "❌ Not Attending"}


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.user_id == bot.user.id:
        return
    emoji_str = str(payload.emoji)
    if emoji_str not in EVENT_RSVP_LABELS:
        return

    record = db.get_event_message(payload.message_id)
    if record is None:
        return
    origin_channel_id, event_name = record

    origin_channel = bot.get_channel(origin_channel_id)
    if origin_channel is None:
        try:
            origin_channel = await bot.fetch_channel(origin_channel_id)
        except discord.HTTPException:
            return

    display = payload.member.mention if payload.member else f"<@{payload.user_id}>"
    label = EVENT_RSVP_LABELS[emoji_str]
    try:
        await origin_channel.send(f"{display} reacted {label} to **{event_name}**.")
    except discord.Forbidden:
        print(f"EVENT_RSVP: missing permission to notify channel {origin_channel_id}")


# ---------- /event ----------
@bot.tree.command(name="event", description="Announce an event (opens a private time picker)")
@app_commands.describe(
    event_type="Type of event (see events.json)",
    details="Optional extra details about the event",
    ping="Which group to ping for this event (only needed if the event type has more than one option)",
    detachment="Also ping and post to a specific detachment's channel (optional, see detachments.json)",
)
@app_commands.autocomplete(event_type=event_type_autocomplete, ping=ping_autocomplete, detachment=detachment_autocomplete)
async def event(interaction: discord.Interaction, event_type: str, details: str = None, ping: str = None, detachment: str = None):
    entry = events_config.find_event(event_type)
    if not entry:
        await interaction.response.send_message(
            f"Unknown event type '{event_type}'. Check events.json for valid names.", ephemeral=True
        )
        return

    required_level = entry.get("clearance")
    if required_level is not None and not events_config.member_has_clearance(interaction.user, required_level):
        await interaction.response.send_message(
            "You don't have a high enough rank to host this event.",
            ephemeral=True,
        )
        return

    pings = entry.get("pings", [])
    primary_role_id = None
    if len(pings) == 1:
        primary_role_id = pings[0].get("role_id")
    elif len(pings) > 1:
        if not ping:
            options = ", ".join(p.get("name", "") for p in pings if p.get("name"))
            await interaction.response.send_message(
                f"This event type has multiple ping options — specify `ping`. Choices: {options}",
                ephemeral=True,
            )
            return
        match = next((p for p in pings if p.get("name", "").strip().lower() == ping.strip().lower()), None)
        if not match:
            await interaction.response.send_message(
                f"Unknown ping option '{ping}' for this event type.", ephemeral=True
            )
            return
        primary_role_id = match.get("role_id")

    if primary_role_id == "PUT_ROLE_ID_HERE":
        primary_role_id = None

    detachment_entry = None
    if detachment:
        detachment_entry = detachments_config.find_detachment(detachment)
        if not detachment_entry:
            await interaction.response.send_message(
                f"Unknown detachment '{detachment}'. Check detachments.json for valid names.", ephemeral=True
            )
            return

    await interaction.response.send_modal(
        EventTimeModal(entry, details, interaction.user, interaction.channel, primary_role_id, detachment_entry)
    )


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


async def _check_site(url: str, timeout_seconds: int = 10) -> bool:
    """Returns True if the site responds with a non-5xx status, False if unreachable or erroring."""
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, allow_redirects=True) as resp:
                return resp.status < 500
    except Exception:
        return False


async def _post_site_status(name: str, url: str, is_up: bool):
    if not MONITOR_CHANNEL_ID:
        print("SITE_MONITOR skipped: MONITOR_CHANNEL_ID is not set.")
        return
    channel = bot.get_channel(int(MONITOR_CHANNEL_ID))
    if channel is None:
        try:
            channel = await bot.fetch_channel(int(MONITOR_CHANNEL_ID))
        except discord.HTTPException:
            print(f"SITE_MONITOR skipped: could not access channel {MONITOR_CHANNEL_ID}.")
            return

    if is_up:
        embed = discord.Embed(title=f"✅ {name} is back online", description=url, color=discord.Color.green())
    else:
        embed = discord.Embed(title=f"🔴 {name} is down", description=url, color=discord.Color.red())
    embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
    await channel.send(embed=embed)


@tasks.loop(minutes=MONITOR_INTERVAL_MINUTES)
async def site_monitor_loop():
    for site in monitor_config.load_sites():
        name = site.get("name")
        url = site.get("url")
        if not name or not url:
            continue

        is_up = await _check_site(url)
        previous = db.get_site_status(name)

        if previous is None:
            # First check ever for this site — just record a baseline. Only alert if it's
            # already down, so a fresh install doesn't spam "back online" for every site.
            db.set_site_status(name, is_up)
            if not is_up:
                await _post_site_status(name, url, is_up)
            continue

        if is_up != previous:
            db.set_site_status(name, is_up)
            await _post_site_status(name, url, is_up)


@site_monitor_loop.before_loop
async def before_site_monitor_loop():
    await bot.wait_until_ready()


# ---------- /sitestatus ----------
@bot.tree.command(name="sitestatus", description="Check the current status of monitored sites right now")
@is_vp_admin()
async def sitestatus(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    sites = monitor_config.load_sites()
    if not sites:
        await interaction.followup.send("No sites configured in monitor_sites.json.", ephemeral=True)
        return
    lines = []
    for site in sites:
        name = site.get("name", "Unknown")
        url = site.get("url", "")
        is_up = await _check_site(url) if url else False
        lines.append(f"{'✅' if is_up else '🔴'} **{name}** — {url}")
    await interaction.followup.send("\n".join(lines), ephemeral=True)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in.")
    bot.run(TOKEN)

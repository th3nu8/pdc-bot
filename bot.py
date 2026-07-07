import os
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

import db

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")  # optional: set for instant command sync during testing
ADMIN_ROLE_ID = os.getenv("VP_ADMIN_ROLE_ID")  # optional: role allowed to manage VP besides Administrators

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


@bot.event
async def on_ready():
    db.init_db()
    if GUILD_ID:
        guild_obj = discord.Object(id=int(GUILD_ID))
        bot.tree.copy_global_to(guild=guild_obj)
        synced = await bot.tree.sync(guild=guild_obj)
    else:
        synced = await bot.tree.sync()
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


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in.")
    bot.run(TOKEN)

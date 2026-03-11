"""
Delta Premium — Discord Bot
"""
import os
import sys
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

sys.path.insert(0, os.path.dirname(__file__))
from database import get_active_key_for_user, get_db, get_user_by_id, init_db, is_user_inactive_pending_discord, list_role_sync_users, redeem_key

SITE_DOMAIN = os.getenv("SITE_DOMAIN", "https://deltapremium.site").rstrip("/")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "MTQ3OTYyODgyMDQ0ODU0NzA5Nw.GLJ66H.yL5LXnMAgx_bSUpFm_1sqc5RepQd1piH_kOQE4")
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "1443300312642359440"))
ROLE_MONTHLY_ID = int(os.getenv("ROLE_MONTHLY_ID", "1479633909552517162"))
ROLE_LIFETIME_ID = int(os.getenv("ROLE_LIFETIME_ID", "1479634137034784949"))
ADMIN_ROLE_NAME = os.getenv("DISCORD_ADMIN_ROLE_NAME", "++Owner")
ROLE_SYNC_INTERVAL_MINUTES = max(int(os.getenv("ROLE_SYNC_INTERVAL_MINUTES", "30")), 5)

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


def plan_role_id(plan: str | None) -> int | None:
    if plan == "monthly":
        return ROLE_MONTHLY_ID
    if plan == "lifetime":
        return ROLE_LIFETIME_ID
    return None


async def sync_member_role(discord_id: str, plan: str | None, should_have_role: bool):
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    member = guild.get_member(int(discord_id))
    if not member:
        return

    target_role_id = plan_role_id(plan) if should_have_role else None
    for role_id in filter(None, [ROLE_MONTHLY_ID, ROLE_LIFETIME_ID]):
        role = guild.get_role(role_id)
        if not role:
            continue
        if role_id != target_role_id and role in member.roles:
            await member.remove_roles(role, reason="Delta Premium role sync")

    if target_role_id:
        role = guild.get_role(target_role_id)
        if role and role not in member.roles:
            await member.add_roles(role, reason="Delta Premium role sync")


async def sync_all_roles():
    for row in list_role_sync_users():
        try:
            await sync_member_role(row["discord_id"], row["plan"], row["should_have_role"])
        except Exception as exc:
            print(f"[Bot] Role sync error for {row.get('discord_id')}: {exc}")


@bot.event
async def on_ready():
    init_db()
    print(f"[Bot] Logged in as {bot.user} ({bot.user.id})")
    try:
        guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        print("[Bot] Slash commands synced")
    except Exception as exc:
        print(f"[Bot] Slash sync error: {exc}")
    if not role_sync_loop.is_running():
        role_sync_loop.start()
    await sync_all_roles()


@tasks.loop(minutes=ROLE_SYNC_INTERVAL_MINUTES)
async def role_sync_loop():
    await sync_all_roles()


@bot.tree.command(name="redeem", description="Redeem your Delta Premium license key")
@app_commands.describe(key="Your license key")
async def slash_redeem(interaction: discord.Interaction, key: str):
    await interaction.response.defer(ephemeral=True)
    discord_id = str(interaction.user.id)
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE discord_id = ?", (discord_id,)).fetchone()
    conn.close()

    if not user:
        await interaction.followup.send(
            f"❌ No Delta Premium account linked to your Discord. Sign up at {SITE_DOMAIN}/signup and link your Discord in the dashboard.",
            ephemeral=True,
        )
        return

    user = dict(user)
    if is_user_inactive_pending_discord(user):
        await interaction.followup.send(
            "❌ Your account is inactive until Discord is linked. Finish linking from the dashboard first.",
            ephemeral=True,
        )
        return

    if get_active_key_for_user(user["id"]):
        await interaction.followup.send("✅ You already have an active premium key.", ephemeral=True)
        return

    result = redeem_key(user["id"], key.strip().upper())
    if not result["success"]:
        await interaction.followup.send(f"❌ {result['error']}", ephemeral=True)
        return

    await sync_member_role(discord_id, result["plan"], True)
    plan_label = "Monthly" if result["plan"] == "monthly" else "Lifetime"
    expires_msg = f"\nExpires: {result['expires_at'][:10]}" if result.get("expires_at") else "\nAccess: Lifetime ♾️"
    await interaction.followup.send(
        f"🎉 **Delta Premium Activated**\nPlan: **{plan_label}**{expires_msg}\nDashboard: {SITE_DOMAIN}/dashboard",
        ephemeral=True,
    )


@bot.tree.command(name="status", description="Check your Delta Premium status")
async def slash_status(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    discord_id = str(interaction.user.id)
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE discord_id = ?", (discord_id,)).fetchone()
    conn.close()
    if not user:
        await interaction.followup.send("❌ No account linked to your Discord.", ephemeral=True)
        return
    user = dict(user)
    key = get_active_key_for_user(user["id"])
    if not key:
        await interaction.followup.send("⛔ No active premium access.", ephemeral=True)
        return
    plan = "Monthly" if key["plan"] == "monthly" else "Lifetime"
    exp = key["expires_at"][:10] if key.get("expires_at") else "Never"
    extra = "\n⚠️ Account inactive until Discord is linked." if is_user_inactive_pending_discord(user) else ""
    await interaction.followup.send(f"✅ **Delta Premium Active**\nPlan: {plan}\nExpires: {exp}{extra}", ephemeral=True)


@bot.tree.command(name="dm-key", description="[Admin] Send a key to a user via DM")
@app_commands.describe(user="Discord user", key="License key to send")
async def slash_dm_key(interaction: discord.Interaction, user: discord.Member, key: str):
    if not any(r.name == ADMIN_ROLE_NAME for r in interaction.user.roles):
        await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        return
    try:
        await user.send(
            f"🔑 **Your Delta Premium Key**\n```{key}```\nRedeem at {SITE_DOMAIN}/dashboard or use `/redeem {key}` in Discord."
        )
        await interaction.response.send_message(f"✅ Key sent to {user.mention}", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ Could not DM that user.", ephemeral=True)
    except Exception as exc:
        await interaction.response.send_message(f"❌ Error: {exc}", ephemeral=True)


@bot.command(name="addtime")
@commands.has_role(ADMIN_ROLE_NAME)
async def cmd_addtime(ctx, member: discord.Member, days: int):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE discord_id = ?", (str(member.id),)).fetchone()
    conn.close()
    if not user:
        await ctx.send("❌ User not found in database.")
        return
    user = dict(user)
    key = get_active_key_for_user(user["id"])
    if not key:
        await ctx.send("⚠️ That user has no active key to extend.")
        return
    cur = datetime.fromisoformat(key["expires_at"]) if key.get("expires_at") else datetime.utcnow()
    new_exp = (cur + timedelta(days=days)).isoformat()
    conn = get_db()
    conn.execute("UPDATE keys SET expires_at = ? WHERE id = ?", (new_exp, key["id"]))
    conn.commit()
    conn.close()
    await ctx.send(f"✅ Added {days} days to {member.mention}. New expiry: {new_exp[:10]}")


if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)

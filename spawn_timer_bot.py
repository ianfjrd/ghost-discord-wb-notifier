import os
import json
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks
from discord import app_commands

# ---------------- CONFIG ----------------
DATA_FILE = "spawns.json"
SPAWN_INTERVAL_HOURS = 2
CHECK_INTERVAL_SECONDS = 5

UTC = timezone.utc
MANILA = timezone(timedelta(hours=8))  # Asia/Manila

# ---------------- TIME HELPERS ----------------
def now_utc() -> datetime:
    return datetime.now(tz=UTC)

def format_time(dt: datetime) -> str:
    unix = int(dt.timestamp())
    return f"<t:{unix}:F> • <t:{unix}:R>"

def parse_time(inp: str) -> datetime | None:
    """
    Accepts:
      +120        (minutes)
      +2h / +90m  (relative)
      2026-03-03 18:30  (absolute, assumed Asia/Manila)
    """
    inp = (inp or "").strip()
    if not inp:
        return None

    if inp.startswith("+"):
        val = inp[1:].strip().lower()
        if val.isdigit():
            return now_utc() + timedelta(minutes=int(val))
        if val.endswith("h") and val[:-1].isdigit():
            return now_utc() + timedelta(hours=int(val[:-1]))
        if val.endswith("m") and val[:-1].isdigit():
            return now_utc() + timedelta(minutes=int(val[:-1]))
        return None

    try:
        dt_local = datetime.strptime(inp, "%Y-%m-%d %H:%M").replace(tzinfo=MANILA)
        return dt_local.astimezone(UTC)
    except ValueError:
        return None

# ---------------- STORAGE ----------------
def load_data() -> dict:
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data: dict):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

store = load_data()

def get_rec(guild_id: int) -> dict:
    return store.get(str(guild_id), {})

def set_rec(guild_id: int, rec: dict):
    store[str(guild_id)] = rec
    save_data(store)

def clear_rec(guild_id: int) -> bool:
    gid = str(guild_id)
    if gid in store:
        del store[gid]
        save_data(store)
        return True
    return False

def get_next_spawn(guild_id: int) -> datetime | None:
    rec = get_rec(guild_id)
    iso = rec.get("next_spawn")
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).astimezone(UTC)
    except ValueError:
        return None

def set_next_spawn(guild_id: int, dt: datetime, channel_id: int | None):
    rec = get_rec(guild_id)
    rec["next_spawn"] = dt.astimezone(UTC).isoformat()
    if channel_id is not None:
        rec["channel_id"] = int(channel_id)
    rec["notified"] = False
    set_rec(guild_id, rec)

def mark_notified(guild_id: int):
    rec = get_rec(guild_id)
    rec["notified"] = True
    set_rec(guild_id, rec)

# ---------------- DISCORD BOT ----------------
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

class SpawnView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)  # 10 minutes; buttons still work while bot running

    @discord.ui.button(label="World Boss Down (Start 2h)", style=discord.ButtonStyle.success)
    async def boss_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        ns = now_utc() + timedelta(hours=SPAWN_INTERVAL_HOURS)
        set_next_spawn(interaction.guild_id, ns, interaction.channel_id)
        await interaction.response.send_message(
            f"✅World Boss marked as down.\nNext spawn: {format_time(ns)}\n📣 Alert channel: <#{interaction.channel_id}>"
        )

    @discord.ui.button(label="Show Timer", style=discord.ButtonStyle.primary)
    async def show_timer(self, interaction: discord.Interaction, button: discord.ui.Button):
        ns = get_next_spawn(interaction.guild_id)
        if not ns:
            await interaction.response.send_message(
                "ℹ️ No timer set yet. Click **Boss Down** when it dies.",
                ephemeral=True
            )
            return
        await interaction.response.send_message(f"⏳ Next Boss spawn: {format_time(ns)}")

    @discord.ui.button(label="How to Edit Time", style=discord.ButtonStyle.secondary)
    async def edit_help(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Use:\n"
            "`/setspawn time:+120`\n"
            "`/setspawn time:+2h`\n"
            "`/setspawn time:2026-03-03 18:30` (Manila time)\n",
            ephemeral=True
        )

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (id={bot.user.id})")

    guild_id = os.getenv("GUILD_ID")
    try:
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            synced = await bot.tree.sync(guild=guild)
            print(f"✅ Synced {len(synced)} commands to guild {guild_id}")
        else:
            synced = await bot.tree.sync()
            print(f"✅ Synced {len(synced)} global commands")
    except Exception as e:
        print("❌ Slash sync error:", repr(e))

    if not spawn_watcher.is_running():
        spawn_watcher.start()

# ---------------- AUTO SPAWN NOTIFIER ----------------
@tasks.loop(seconds=CHECK_INTERVAL_SECONDS)
async def spawn_watcher():
    for gid_str, rec in list(store.items()):
        try:
            guild_id = int(gid_str)
        except ValueError:
            continue

        next_spawn_iso = rec.get("next_spawn")
        channel_id = rec.get("channel_id")
        notified = bool(rec.get("notified", False))

        if not next_spawn_iso or not channel_id or notified:
            continue

        try:
            ns = datetime.fromisoformat(next_spawn_iso).astimezone(UTC)
        except ValueError:
            continue

        if now_utc() >= ns:
            channel = bot.get_channel(int(channel_id))
            if channel is None:
                mark_notified(guild_id)
                continue

            try:
                await channel.send(f"🔔 **WORLD BOSS SPAWN NOW!** !** <@&1425512694953541843>\nScheduled spawn time: {format_time(ns)}")
            finally:
                mark_notified(guild_id)

# ---------------- SLASH COMMANDS ----------------
@bot.tree.command(name="spawnpanel", description="Show the Boss spawn panel (also sets this channel as alert channel).")
async def spawnpanel(interaction: discord.Interaction):
    # Save current channel as alert channel
    rec = get_rec(interaction.guild_id)
    rec["channel_id"] = int(interaction.channel_id)
    set_rec(interaction.guild_id, rec)

    await interaction.response.send_message(
        f"📌 **World Boss Spawn Timer**\n📣 Alert channel set to: <#{interaction.channel_id}>",
        view=SpawnView()
    )

@bot.tree.command(name="setspawn", description="Manually set next spawn time (also sets this channel as alert channel).")
@app_commands.describe(time="Use +minutes, +2h, or YYYY-MM-DD HH:MM (Manila)")
async def setspawn(interaction: discord.Interaction, time: str):
    dt = parse_time(time)
    if not dt:
        await interaction.response.send_message(
            "❌ Invalid format.\nUse `+120`, `+2h`, or `YYYY-MM-DD HH:MM` (Manila).",
            ephemeral=True
        )
        return

    set_next_spawn(interaction.guild_id, dt, interaction.channel_id)
    await interaction.response.send_message(
        f"✅ Spawn updated: {format_time(dt)}\n📣 Alert channel: <#{interaction.channel_id}>"
    )

@bot.tree.command(name="clearspawn", description="Clear the spawn timer.")
async def clearspawn(interaction: discord.Interaction):
    if clear_rec(interaction.guild_id):
        await interaction.response.send_message("🗑️ Spawn timer cleared.")
    else:
        await interaction.response.send_message("ℹ️ No timer set.", ephemeral=True)

# ---------------- RUN ----------------
if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("Set DISCORD_TOKEN first (same terminal).")
    bot.run(token)

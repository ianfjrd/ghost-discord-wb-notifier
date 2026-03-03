import discord
from discord import app_commands
from discord.ext import commands, tasks
import json
import os
from datetime import datetime, timedelta, timezone

DATA_FILE = "spawns.json"
SPAWN_INTERVAL_HOURS = 2

UTC = timezone.utc

def now_utc() -> datetime:
    return datetime.now(tz=UTC)

def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def guild_key(guild_id: int) -> str:
    return str(guild_id)

def boss_key(name: str) -> str:
    return name.strip().lower() if name else "default"

def dt_to_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()

def iso_to_dt(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(UTC)

def format_discord_timestamp(dt: datetime) -> str:
    # shows both absolute and relative time in Discord
    unix = int(dt.timestamp())
    return f"<t:{unix}:F>  •  <t:{unix}:R>"

class SpawnStore:
    def __init__(self):
        self.data = load_data()

    def get_next_spawn(self, guild_id: int, boss: str) -> datetime | None:
        g = self.data.get(guild_key(guild_id), {})
        b = g.get(boss_key(boss))
        if not b:
            return None
        return iso_to_dt(b["next_spawn"])

    def set_next_spawn(self, guild_id: int, boss: str, next_spawn: datetime):
        gid = guild_key(guild_id)
        bk = boss_key(boss)
        if gid not in self.data:
            self.data[gid] = {}
        self.data[gid][bk] = {"next_spawn": dt_to_iso(next_spawn)}
        save_data(self.data)

store = SpawnStore()

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

class SpawnView(discord.ui.View):
    def __init__(self, boss_name: str):
        super().__init__(timeout=None)
        self.boss_name = boss_name

    @discord.ui.button(label="Boss Down (Start 2h)", style=discord.ButtonStyle.success, custom_id="spawn:bossdown")
    async def boss_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        ns = now_utc() + timedelta(hours=SPAWN_INTERVAL_HOURS)
        store.set_next_spawn(interaction.guild_id, self.boss_name, ns)
        await interaction.response.send_message(
            f"✅ **{self.boss_name}** marked as down.\nNext spawn: {format_discord_timestamp(ns)}",
            ephemeral=False
        )

    @discord.ui.button(label="Show Timer", style=discord.ButtonStyle.primary, custom_id="spawn:show")
    async def show_timer(self, interaction: discord.Interaction, button: discord.ui.Button):
        ns = store.get_next_spawn(interaction.guild_id, self.boss_name)
        if not ns:
            await interaction.response.send_message(
                f"ℹ️ No timer set yet for **{self.boss_name}**. Click **Boss Down** when it dies.",
                ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"⏳ **{self.boss_name}** next spawn: {format_discord_timestamp(ns)}",
            ephemeral=False
        )

    @discord.ui.button(label="Edit Time (Forgot to click)", style=discord.ButtonStyle.secondary, custom_id="spawn:edithelp")
    async def edit_help(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "To edit manually, use:\n"
            f"**/setspawn boss:{self.boss_name} time:** `YYYY-MM-DD HH:MM` (24h) **OR** `+minutes` like `+15` **OR** `+hours` like `+2`\n"
            "Examples:\n"
            "`/setspawn boss:enigma time:+120`\n"
            "`/setspawn boss:enigma time:2026-03-03 18:30`",
            ephemeral=True
        )

@bot.event
async def on_ready():
    # Register persistent views so buttons keep working after restart
    bot.add_view(SpawnView("default"))
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print("Slash sync error:", e)

@bot.tree.command(name="spawnpanel", description="Post the boss spawn timer panel with buttons.")
@app_commands.describe(boss="Boss name (optional). Example: enigma, sunstone boss, etc.")
async def spawnpanel(interaction: discord.Interaction, boss: str = "default"):
    view = SpawnView(boss)
    bot.add_view(view)  # ensure it's persistent
    await interaction.response.send_message(
        f"📌 **Spawn Timer Panel — {boss}**\nUse the buttons below:",
        view=view
    )

def parse_time_input(inp: str) -> datetime | None:
    inp = inp.strip()
    # Relative: +number (minutes by default)
    if inp.startswith("+"):
        val = inp[1:].strip()
        if val.isdigit():
            mins = int(val)
            return now_utc() + timedelta(minutes=mins)
        # +2h or +120m style
        val = val.lower()
        if val.endswith("h") and val[:-1].isdigit():
            return now_utc() + timedelta(hours=int(val[:-1]))
        if val.endswith("m") and val[:-1].isdigit():
            return now_utc() + timedelta(minutes=int(val[:-1]))
        return None

    # Absolute: YYYY-MM-DD HH:MM (assume Asia/Manila -> UTC+8)
    # We’ll convert it to UTC for storage.
    try:
        local_tz = timezone(timedelta(hours=8))  # Asia/Manila fixed offset
        dt_local = datetime.strptime(inp, "%Y-%m-%d %H:%M").replace(tzinfo=local_tz)
        return dt_local.astimezone(UTC)
    except ValueError:
        return None

@bot.tree.command(name="setspawn", description="Manually set the next spawn time (if you forgot to click).")
@app_commands.describe(
    boss="Boss name (optional).",
    time="Either 'YYYY-MM-DD HH:MM' (Asia/Manila) or '+minutes' like +120 or +2h"
)
async def setspawn(interaction: discord.Interaction, boss: str = "default", time: str = ""):
    dt = parse_time_input(time)
    if not dt:
        await interaction.response.send_message(
            "❌ Invalid time.\nUse `YYYY-MM-DD HH:MM` (Asia/Manila) or `+120` (minutes) or `+2h`.",
            ephemeral=True
        )
        return
    store.set_next_spawn(interaction.guild_id, boss, dt)
    await interaction.response.send_message(
        f"✅ Set **{boss}** next spawn to: {format_discord_timestamp(dt)}",
        ephemeral=False
    )

@bot.tree.command(name="clearspawn", description="Clear the spawn timer for a boss.")
@app_commands.describe(boss="Boss name (optional).")
async def clearspawn(interaction: discord.Interaction, boss: str = "default"):
    gid = guild_key(interaction.guild_id)
    bk = boss_key(boss)
    if gid in store.data and bk in store.data[gid]:
        del store.data[gid][bk]
        save_data(store.data)
        await interaction.response.send_message(f"🗑️ Cleared timer for **{boss}**.", ephemeral=False)
    else:
        await interaction.response.send_message(f"ℹ️ No timer found for **{boss}**.", ephemeral=True)

# ---- RUN ----
# Put your token in an environment variable for safety:
# Windows (PowerShell): $env:DISCORD_TOKEN="YOUR_TOKEN"
# Linux/macOS: export DISCORD_TOKEN="YOUR_TOKEN"
token = os.getenv("DISCORD_TOKEN")
if not token:
    raise RuntimeError("Set DISCORD_TOKEN env var first.")
bot.run(token)
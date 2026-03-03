import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

import discord
from discord.ext import commands
from discord import app_commands


# -------------------------
# Render needs a port open
# -------------------------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        return


def run_web_server():
    port = int(os.environ.get("PORT", "10000"))
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()


# -------------------------
# Discord bot
# -------------------------
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (id={bot.user.id})")

    # Fast command registration: if GUILD_ID is set, sync instantly to that server
    guild_id = os.getenv("GUILD_ID")
    try:
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            synced = await bot.tree.sync(guild=guild)
            print(f"✅ Synced {len(synced)} commands to guild {guild_id}")
        else:
            synced = await bot.tree.sync()
            print(f"✅ Synced {len(synced)} global commands (may take time to appear)")
    except Exception as e:
        print("❌ Slash sync error:", repr(e))


@bot.tree.command(name="ping", description="Test if the bot is alive.")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong! ✅")


if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is missing in Render environment variables.")

    # Start health server thread so Render Web Service stays alive
    threading.Thread(target=run_web_server, daemon=True).start()

    # Start Discord bot
    bot.run(token)

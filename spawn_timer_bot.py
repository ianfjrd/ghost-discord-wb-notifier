import os
import threading
import asyncio
from http.server import HTTPServer, BaseHTTPRequestHandler

import discord
from discord.ext import commands
from discord import app_commands
from discord.errors import HTTPException


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


@bot.tree.command(name="ping", description="Test if the bot is alive.")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong! ✅")


async def run_bot_forever(token: str):
    delay = 30  # start with 30s to be gentle
    while True:
        try:
            await bot.start(token)
        except HTTPException as e:
            # Includes 429. Don't exit; wait and retry.
            print(f"Discord HTTPException (likely rate limit): {e}")
        except Exception as e:
            print(f"Bot error: {repr(e)}")

        print(f"Retrying login in {delay} seconds...")
        await asyncio.sleep(delay)
        delay = min(delay * 2, 900)  # max 15 minutes


if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is missing in Render environment variables.")

    threading.Thread(target=run_web_server, daemon=True).start()
    asyncio.run(run_bot_forever(token))

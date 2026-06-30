import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

# Load token from .env file (used for local testing)
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# Set up the bot with the intents we enabled in the Developer Portal
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("Bot is online and ready!")


@bot.command()
async def ping(ctx):
    """Simple test command to check the bot is responsive."""
    await ctx.send("Pong! 🏓 The bot is working.")


@bot.command()
async def hello(ctx):
    """Greets the user who ran the command."""
    await ctx.send(f"Hey {ctx.author.mention}, welcome to the carry server!")


if __name__ == "__main__":
    if not TOKEN:
        raise ValueError("No DISCORD_TOKEN found. Did you set it in .env or Railway variables?")
    bot.run(TOKEN)

import os
import discord
from discord import app_commands
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

# ----- Carry system config -----
CARRY_ANNOUNCEMENT_CHANNEL_ID = 1521342487501865031
CARRIER_ROLE_ID = 1521346647320432793

CARRY_TYPE_ROLES = {
    "Enmity": 1521339196223131758,
    "Titus": 1521339291714850867,
    "Elder Primadon": 1521339319745253486,
    "Other": 1521339358450548928,
}

# Tracks which carriers currently have an active carry: {user_id: True}
active_carries = {}


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s).")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    print("Bot is online and ready!")


@bot.tree.command(name="startcarry", description="Start a carry and announce it in the carry channel.")
@app_commands.describe(type_of_carry="The type of carry you are starting")
@app_commands.choices(type_of_carry=[
    app_commands.Choice(name="Enmity", value="Enmity"),
    app_commands.Choice(name="Titus", value="Titus"),
    app_commands.Choice(name="Elder Primadon", value="Elder Primadon"),
    app_commands.Choice(name="Other", value="Other"),
])
async def startcarry(interaction: discord.Interaction, type_of_carry: app_commands.Choice[str]):
    # Make sure the person using the command has the carrier role
    carrier_role = interaction.guild.get_role(CARRIER_ROLE_ID)
    if carrier_role not in interaction.user.roles:
        await interaction.response.send_message(
            "You need the Carrier role to start a carry.", ephemeral=True
        )
        return

    # One carrier can't have multiple carries active at once
    if active_carries.get(interaction.user.id):
        await interaction.response.send_message(
            "You already have an active carry. End it with /endcarry before starting another.",
            ephemeral=True,
        )
        return

    active_carries[interaction.user.id] = True

    carry_type = type_of_carry.value
    role_id = CARRY_TYPE_ROLES.get(carry_type)
    role_mention = f"<@&{role_id}>" if role_id else ""

    channel = bot.get_channel(CARRY_ANNOUNCEMENT_CHANNEL_ID)
    if channel is None:
        await interaction.response.send_message(
            "Couldn't find the carry announcement channel. Check the channel ID in the bot config.",
            ephemeral=True,
        )
        active_carries.pop(interaction.user.id, None)
        return

    embed = discord.Embed(
        title="🛡️ Carry Starting!",
        description=f"A **{carry_type}** carry is starting now!",
        color=discord.Color.blue(),
    )
    embed.add_field(name="Carrier", value=interaction.user.mention, inline=True)
    embed.add_field(name="Type Of Carry", value=carry_type, inline=True)
    embed.set_footer(text="Use /endcarry to mark this carry as finished.")

    await channel.send(content=role_mention, embed=embed)
    await interaction.response.send_message(
        f"Carry announcement sent in {channel.mention}!", ephemeral=True
    )


@bot.tree.command(name="endcarry", description="End your active carry.")
async def endcarry(interaction: discord.Interaction):
    if not active_carries.get(interaction.user.id):
        await interaction.response.send_message(
            "You don't have an active carry right now.", ephemeral=True
        )
        return

    active_carries.pop(interaction.user.id, None)
    await interaction.response.send_message("Ended")


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

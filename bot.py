import os
import time
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
intents.voice_states = True

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

# ----- Drag system config -----
DRAG_SOURCE_CHANNEL_ID = 1521341265793388677

# ----- Vouch system config -----
VOUCH_CHANNEL_ID = 1521340189857939497
VOUCH_COOLDOWN_SECONDS = 60
# This tag is embedded (invisibly, via a hidden marker) in every vouch message
# the bot sends, so /viewvouches can find and count them later.
VOUCH_TAG = "VOUCHRECORD"

# Tracks last time each user used /vouch successfully: {user_id: timestamp}
vouch_cooldowns = {}


def pluralize_vouch(count):
    """Returns 'vouch' for 1, 'vouches' for anything else."""
    return "vouch" if count == 1 else "vouches"


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


def has_carrier_role(member: discord.Member) -> bool:
    role = discord.utils.get(member.roles, id=CARRIER_ROLE_ID)
    return role is not None


@bot.tree.command(name="drag", description="Pull someone from the drag channel into your voice/stage channel.")
@app_commands.describe(who_to_drag="The user to drag (must be in the drag-from channel)")
async def drag(interaction: discord.Interaction, who_to_drag: discord.Member):
    if not has_carrier_role(interaction.user):
        await interaction.response.send_message(
            "You need the Carrier role to use this command.", ephemeral=True
        )
        return

    # The carrier must themselves be in a voice/stage channel to drag someone to it
    if interaction.user.voice is None or interaction.user.voice.channel is None:
        await interaction.response.send_message(
            "You need to be in a voice or stage channel to drag someone to it.",
            ephemeral=True,
        )
        return

    destination = interaction.user.voice.channel

    # Only allow dragging users who are actually connected to the designated channel
    if (
        who_to_drag.voice is None
        or who_to_drag.voice.channel is None
        or who_to_drag.voice.channel.id != DRAG_SOURCE_CHANNEL_ID
    ):
        await interaction.response.send_message(
            f"{who_to_drag.mention} isn't connected to the drag-from channel.",
            ephemeral=True,
        )
        return

    try:
        await who_to_drag.move_to(destination)
    except discord.Forbidden:
        await interaction.response.send_message(
            "I don't have permission to move that member. Check my role permissions.",
            ephemeral=True,
        )
        return
    except discord.HTTPException as e:
        await interaction.response.send_message(
            f"Something went wrong while dragging that user: {e}", ephemeral=True
        )
        return

    await interaction.response.send_message(
        f"Dragged {who_to_drag.mention} into {destination.mention}.", ephemeral=True
    )


@bot.tree.command(name="massdrag", description="Pull everyone from the drag channel into your voice/stage channel.")
async def massdrag(interaction: discord.Interaction):
    if not has_carrier_role(interaction.user):
        await interaction.response.send_message(
            "You need the Carrier role to use this command.", ephemeral=True
        )
        return

    if interaction.user.voice is None or interaction.user.voice.channel is None:
        await interaction.response.send_message(
            "You need to be in a voice or stage channel to drag people to it.",
            ephemeral=True,
        )
        return

    destination = interaction.user.voice.channel

    source_channel = interaction.guild.get_channel(DRAG_SOURCE_CHANNEL_ID)
    if source_channel is None:
        await interaction.response.send_message(
            "Couldn't find the drag-from channel. Check the channel ID in the bot config.",
            ephemeral=True,
        )
        return

    members_to_drag = list(source_channel.members)
    if not members_to_drag:
        await interaction.response.send_message(
            "There's no one in the drag-from channel right now.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    dragged = 0
    failed = 0
    for member in members_to_drag:
        try:
            await member.move_to(destination)
            dragged += 1
        except (discord.Forbidden, discord.HTTPException):
            failed += 1

    summary = f"Dragged {dragged} member(s) into {destination.mention}."
    if failed:
        summary += f" Failed to move {failed} member(s)."

    await interaction.followup.send(summary, ephemeral=True)


@bot.tree.command(name="vouch", description="Vouch for someone in the vouch channel.")
@app_commands.describe(who_to_vouch="The user you want to vouch for")
async def vouch(interaction: discord.Interaction, who_to_vouch: discord.User):
    # Restrict this command to the designated vouch channel only.
    # Using it outside that channel does NOT count against the cooldown.
    if interaction.channel_id != VOUCH_CHANNEL_ID:
        channel = bot.get_channel(VOUCH_CHANNEL_ID)
        location = channel.mention if channel else "the vouch channel"
        await interaction.response.send_message(
            f"You can only use /vouch in {location}.", ephemeral=True
        )
        return

    # Check the 1-minute cooldown (per user, only applies when used in the right channel)
    now = time.time()
    last_used = vouch_cooldowns.get(interaction.user.id)
    if last_used and (now - last_used) < VOUCH_COOLDOWN_SECONDS:
        remaining = int(VOUCH_COOLDOWN_SECONDS - (now - last_used))
        await interaction.response.send_message(
            f"You're on cooldown. Try again in {remaining} second(s).", ephemeral=True
        )
        return

    if who_to_vouch.id == interaction.user.id:
        await interaction.response.send_message(
            "You can't vouch for yourself.", ephemeral=True
        )
        return

    vouch_cooldowns[interaction.user.id] = now

    # Send the actual vouch record into the channel. The hidden VOUCH_TAG/target marker
    # is what /viewvouches looks for later to count vouches — it's invisible to users
    # since it's tucked into the embed footer.
    embed = discord.Embed(
        description=f"✅ {interaction.user.mention} vouched for {who_to_vouch.mention}!",
        color=discord.Color.green(),
    )
    embed.set_footer(text=str(who_to_vouch.id))

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="viewvouches", description="Check how many vouches someone has.")
@app_commands.describe(user="The user to check (leave empty to check yourself)")
async def viewvouches(interaction: discord.Interaction, user: discord.User = None):
    target = user or interaction.user

    channel = bot.get_channel(VOUCH_CHANNEL_ID)
    if channel is None:
        await interaction.response.send_message(
            "Couldn't find the vouch channel. Check the channel ID in the bot config.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    target_marker = str(target.id)
    count = 0

    # Scan the vouch channel's history, but ONLY count messages sent by this bot.
    # We also check the embed color matches our vouch embeds, so we don't
    # accidentally match some other unrelated bot embed that happens to have
    # the same number in its footer.
    async for message in channel.history(limit=None):
        if message.author.id != bot.user.id:
            continue
        for embed in message.embeds:
            footer_text = embed.footer.text if embed.footer else ""
            if footer_text == target_marker and embed.color == discord.Color.green():
                count += 1

    await interaction.followup.send(
        f"{target.mention} has **{count}** {pluralize_vouch(count)}.", ephemeral=True
    )



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

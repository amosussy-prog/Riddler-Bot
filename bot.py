import os
import time
import asyncio
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

# Tracks which carriers currently have an active carry: {user_id: {"start": ts, "type": str}}
active_carries = {}

CARRY_LOG_CHANNEL_ID = 1521356371482906675

# ----- Drag system config -----
DRAG_SOURCE_CHANNEL_ID = 1521341265793388677

# ----- Ticket system config -----
OWNER_ID = 1290758651652603995
OVERSEER_ROLE_ID = 1521357864793280512

TICKET_TYPES = ["Carry Ticket", "Support Ticket", "Ally Ticket"]

# ----- Vouch system config -----
VOUCH_CHANNEL_ID = 1521340189857939497
VOUCH_COOLDOWN_SECONDS = 60
# This tag is embedded (invisibly, via a hidden marker) in every vouch message
# the bot sends, so /viewvouches can find and count them later.
VOUCH_TAG = "VOUCHRECORD"

# Tracks last time each user used /vouch successfully: {user_id: timestamp}
vouch_cooldowns = {}

SUPPORT_VOUCH_COOLDOWN_SECONDS = 600  # 10 minutes

# Tracks last time each carrier used /supportvouch successfully: {user_id: timestamp}
support_vouch_cooldowns = {}


def pluralize_vouch(count):
    """Returns 'vouch' for 1, 'vouches' for anything else."""
    return "vouch" if count == 1 else "vouches"


def pluralize_carry(count):
    """Returns 'carry' for 1, 'carries' for anything else."""
    return "carry" if count == 1 else "carries"


def format_duration(seconds):
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def is_overseer_or_above(member: discord.Member) -> bool:
    """True if the member has the Overseer role, or any role positioned higher than it."""
    overseer_role = member.guild.get_role(OVERSEER_ROLE_ID)
    if overseer_role is None:
        return False
    if overseer_role in member.roles:
        return True
    return member.top_role.position > overseer_role.position


async def count_marked_messages(channel_id: int, target_id: int, color: discord.Color) -> int:
    """Counts bot-sent embeds in a channel whose footer matches target_id and whose
    color matches the given marker color. Shared by vouches/carries/support-vouches/promote."""
    channel = bot.get_channel(channel_id)
    if channel is None:
        return 0

    target_marker = str(target_id)
    count = 0
    async for message in channel.history(limit=None):
        if message.author.id != bot.user.id:
            continue
        for embed in message.embeds:
            footer_text = embed.footer.text if embed.footer else ""
            if footer_text == target_marker and embed.color == color:
                count += 1
    return count


# ----- Promotion system config -----
BASE_SUPPORT_ROLE_ID = 1521355590993973308

# Each entry: (role_id, role_name, carries_required, vouches_required)
# Ordered from highest rank to lowest. Trial Carrier has no requirements (starting role).
CARRIER_LADDER = [
    (1521358275453386793, "Drowned God", 100, 1000),
    (1521357864793280512, "Overseer", 75, 800),
    (1521357464866525244, "Godly Carrier", 50, 650),
    (1521357362655399941, "Advanced Carrier", 30, 400),
    (1521357135404073191, "Experienced Carrier", 10, 150),
    (1521356147054088252, "Apprentice Carrier", 3, 50),
    (1521355970884931664, "Trial Carrier", 0, 0),
]

# Each entry: (role_id, role_name, support_vouches_required, vouches_required)
SUPPORT_LADDER = [
    (1521358753469825126, "Celestial of Support", 100, 750),
    (1521358650533478461, "Vowed Support", 50, 500),
    (1521364861421092956, "Advanced Support", 30, 350),
    (1521358581054574683, "Experienced Support", 15, 250),
    (1521358496397000734, "Apprentice Support", 5, 100),
    (1521356092632862725, "Trial Support", 0, 0),
]


TICKET_INSTRUCTIONS = {
    "Ally Ticket": (
        "Send the following information to become an ally.\n\n"
        "* Guild\n"
        "* Members\n"
        "* Why do you want to be an ally?\n\n"
        "And an owner will be with you shortly."
    ),
    "Carry Ticket": (
        "Send evidence of you defeating the following without going below 60% health:\n\n"
        "* Enmity\n"
        "* Elder Primadon\n"
        "* Titus\n"
        "* Kyrsgarde Champion\n\n"
        "And an overseer will be with you shortly."
    ),
    "Support Ticket": (
        "Send evidence of supporting a carrier or ping the carrier you supported to vouch for you, "
        "and an overseer will be with you shortly."
    ),
}


async def create_ticket_channel(interaction: discord.Interaction, ticket_type: str):
    guild = interaction.guild
    category = interaction.channel.category  # Create the ticket in the same category as the panel

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True),
    }

    overseer_role = guild.get_role(OVERSEER_ROLE_ID)
    if overseer_role:
        # Grant access to Overseer and every role positioned above it
        for role in guild.roles:
            if role.position >= overseer_role.position and role != guild.default_role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True
                )

    channel_name = f"{interaction.user.name}| {ticket_type}"

    channel = await guild.create_text_channel(
        name=channel_name,
        category=category,
        overwrites=overwrites,
        reason=f"Ticket opened by {interaction.user} ({interaction.user.id})",
    )

    embed = discord.Embed(
        title=f"🎫 {ticket_type}",
        description=TICKET_INSTRUCTIONS.get(ticket_type, "Please describe what you need help with below."),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=f"Ticket owner: {interaction.user.id}")

    await channel.send(content=interaction.user.mention, embed=embed, view=TicketControlView())

    return channel


class TicketPanelView(discord.ui.View):
    """The persistent panel with the 3 ticket-type buttons. Posted once via /createticketsystem."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Carry Ticket", style=discord.ButtonStyle.primary, custom_id="ticket_panel_carry")
    async def carry_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        channel = await create_ticket_channel(interaction, "Carry Ticket")
        await interaction.followup.send(f"Your ticket has been created: {channel.mention}", ephemeral=True)

    @discord.ui.button(label="Support Ticket", style=discord.ButtonStyle.secondary, custom_id="ticket_panel_support")
    async def support_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        channel = await create_ticket_channel(interaction, "Support Ticket")
        await interaction.followup.send(f"Your ticket has been created: {channel.mention}", ephemeral=True)

    @discord.ui.button(label="Ally Ticket", style=discord.ButtonStyle.success, custom_id="ticket_panel_ally")
    async def ally_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        channel = await create_ticket_channel(interaction, "Ally Ticket")
        await interaction.followup.send(f"Your ticket has been created: {channel.mention}", ephemeral=True)


class TicketControlView(discord.ui.View):
    """Persistent Close/Claim buttons posted inside each ticket channel."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.secondary, custom_id="ticket_control_claim")
    async def claim_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_overseer_or_above(interaction.user):
            await interaction.response.send_message(
                "Only Overseers (or higher) can claim tickets.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"🙋 {interaction.user.mention} has claimed this ticket."
        )

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, custom_id="ticket_control_close")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_overseer_or_above(interaction.user):
            await interaction.response.send_message(
                "Only Overseers (or higher) can close tickets.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            "🔒 Closing this ticket in 5 seconds..."
        )
        await asyncio.sleep(5)
        await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    bot.add_view(TicketPanelView())
    bot.add_view(TicketControlView())
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s).")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    print("Bot is online and ready!")


@bot.tree.command(name="createticketsystem", description="Post the ticket panel in this channel. Owner only.")
async def createticketsystem(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message(
            "Only the server owner can use this command.", ephemeral=True
        )
        return

    embed = discord.Embed(
        title="🎫 Apply for roles here.",
        description="Click a button below to open a ticket for the relevant team.",
        color=discord.Color.blurple(),
    )

    await interaction.channel.send(embed=embed, view=TicketPanelView())
    await interaction.response.send_message("Ticket panel posted!", ephemeral=True)


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

    carry_type = type_of_carry.value
    active_carries[interaction.user.id] = {"start": time.time(), "type": carry_type}

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
    carry_info = active_carries.get(interaction.user.id)
    if not carry_info:
        await interaction.response.send_message(
            "You don't have an active carry right now.", ephemeral=True
        )
        return

    active_carries.pop(interaction.user.id, None)
    await interaction.response.send_message("Ended")

    duration_seconds = time.time() - carry_info["start"]
    carry_type = carry_info["type"]

    log_channel = bot.get_channel(CARRY_LOG_CHANNEL_ID)
    if log_channel is None:
        return  # Can't log it, but the carry has still ended for the carrier

    log_embed = discord.Embed(
        description=f"🏁 {interaction.user.mention} finished a **{carry_type}** carry.",
        color=discord.Color.orange(),
    )
    log_embed.add_field(name="Duration", value=format_duration(duration_seconds), inline=True)
    log_embed.set_footer(text=str(interaction.user.id))

    await log_channel.send(embed=log_embed)


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

    count = await count_marked_messages(VOUCH_CHANNEL_ID, target.id, discord.Color.green())

    await interaction.followup.send(
        f"{target.mention} has **{count}** {pluralize_vouch(count)}.", ephemeral=True
    )


@bot.tree.command(name="supportvouch", description="Carrier-only: give a support vouch in the vouch channel.")
@app_commands.describe(who_to_vouch="The user you want to give a support vouch to")
async def supportvouch(interaction: discord.Interaction, who_to_vouch: discord.User):
    if not has_carrier_role(interaction.user):
        await interaction.response.send_message(
            "Only carriers can use /supportvouch.", ephemeral=True
        )
        return

    if interaction.channel_id != VOUCH_CHANNEL_ID:
        channel = bot.get_channel(VOUCH_CHANNEL_ID)
        location = channel.mention if channel else "the vouch channel"
        await interaction.response.send_message(
            f"You can only use /supportvouch in {location}.", ephemeral=True
        )
        return

    # 10-minute cooldown, separate from the regular /vouch cooldown
    now = time.time()
    last_used = support_vouch_cooldowns.get(interaction.user.id)
    if last_used and (now - last_used) < SUPPORT_VOUCH_COOLDOWN_SECONDS:
        remaining = int(SUPPORT_VOUCH_COOLDOWN_SECONDS - (now - last_used))
        minutes, seconds = divmod(remaining, 60)
        await interaction.response.send_message(
            f"You're on cooldown. Try again in {minutes}m {seconds}s.", ephemeral=True
        )
        return

    if who_to_vouch.id == interaction.user.id:
        await interaction.response.send_message(
            "You can't vouch for yourself.", ephemeral=True
        )
        return

    support_vouch_cooldowns[interaction.user.id] = now

    # Same pattern as /vouch, but with a distinct color (gold) so it's
    # tracked separately by /viewsupportvouches.
    embed = discord.Embed(
        description=f"⭐ {interaction.user.mention} gave a **support vouch** to {who_to_vouch.mention}!",
        color=discord.Color.gold(),
    )
    embed.set_footer(text=str(who_to_vouch.id))

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="viewsupportvouches", description="Check how many support vouches someone has.")
@app_commands.describe(user="The user to check (leave empty to check yourself)")
async def viewsupportvouches(interaction: discord.Interaction, user: discord.User = None):
    target = user or interaction.user

    channel = bot.get_channel(VOUCH_CHANNEL_ID)
    if channel is None:
        await interaction.response.send_message(
            "Couldn't find the vouch channel. Check the channel ID in the bot config.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    count = await count_marked_messages(VOUCH_CHANNEL_ID, target.id, discord.Color.gold())

    await interaction.followup.send(
        f"{target.mention} has **{count}** support {pluralize_vouch(count)}.", ephemeral=True
    )


@bot.tree.command(name="promote", description="Check your stats and get promoted if you qualify.")
async def promote(interaction: discord.Interaction):
    target = interaction.user
    guild = interaction.guild

    await interaction.response.defer()

    vouches = await count_marked_messages(VOUCH_CHANNEL_ID, target.id, discord.Color.green())
    carries = await count_marked_messages(CARRY_LOG_CHANNEL_ID, target.id, discord.Color.orange())
    support_vouches = await count_marked_messages(VOUCH_CHANNEL_ID, target.id, discord.Color.gold())

    async def evaluate_ladder(ladder, value1, value2, label1, label2, track_label):
        """Finds the highest tier the user qualifies for, promotes them if it's new,
        and returns (promoted_role_name_or_None, field_name, field_value)."""
        ladder_role_ids = {role_id for role_id, _, _, _ in ladder}
        current_role_ids = {r.id for r in target.roles if r.id in ladder_role_ids}

        qualifying_index = 0
        for i, (role_id, role_name, req1, req2) in enumerate(ladder):
            if value1 >= req1 and value2 >= req2:
                qualifying_index = i
                break

        role_id, role_name, req1, req2 = ladder[qualifying_index]

        promoted_role_name = None
        if role_id not in current_role_ids:
            new_role = guild.get_role(role_id)
            if new_role:
                roles_to_remove = [guild.get_role(rid) for rid in current_role_ids if rid != role_id]
                roles_to_remove = [r for r in roles_to_remove if r]
                if roles_to_remove:
                    await target.remove_roles(*roles_to_remove, reason=f"{track_label} promotion")
                await target.add_roles(new_role, reason=f"{track_label} promotion via /promote")
                promoted_role_name = role_name

        # Stat line only shows the two stats relevant to this track
        stat_value_line = f"{label1}: **{value1}**/**{req1 if qualifying_index == 0 else ladder[qualifying_index - 1][2]}** | {label2}: **{value2}**/**{req2 if qualifying_index == 0 else ladder[qualifying_index - 1][3]}**"

        if qualifying_index == 0:
            field_name = f"{track_label} | Max Rank Reached: {role_name}"
        else:
            next_name = ladder[qualifying_index - 1][1]
            field_name = f"{track_label} | Next Rank Is {next_name}"

        return promoted_role_name, field_name, stat_value_line

    embed = discord.Embed(title=f"📊 Promotion Status — {target.display_name}", color=discord.Color.blurple())
    promotions = []

    # ----- Carrier track: only applies if they already have the base Carrier role -----
    carrier_role = guild.get_role(CARRIER_ROLE_ID)
    if carrier_role and carrier_role in target.roles:
        promoted_role_name, field_name, field_value = await evaluate_ladder(
            CARRIER_LADDER, carries, vouches, "Carries", "Vouches", "Carrier"
        )
        embed.add_field(name=field_name, value=field_value, inline=False)
        if promoted_role_name:
            promotions.append(f"🛡️ Promoted to **{promoted_role_name}**!")

    # ----- Support track: only applies if they already have the base Support role -----
    base_support_role = guild.get_role(BASE_SUPPORT_ROLE_ID)
    if base_support_role and base_support_role in target.roles:
        promoted_role_name, field_name, field_value = await evaluate_ladder(
            SUPPORT_LADDER, support_vouches, vouches, "Support Vouches", "Vouches", "Support"
        )
        embed.add_field(name=field_name, value=field_value, inline=False)
        if promoted_role_name:
            promotions.append(f"❤️ Promoted to **{promoted_role_name}**!")

    if not embed.fields:
        embed.description = "No carrier or support track applies to you yet."
    elif promotions:
        embed.description = "\n".join(promotions)

    await interaction.followup.send(embed=embed)


@bot.tree.command(name="viewcarries", description="Check how many carries a carrier has logged.")
@app_commands.describe(user="The carrier to check (leave empty to check yourself)")
async def viewcarries(interaction: discord.Interaction, user: discord.User = None):
    target = user or interaction.user

    channel = bot.get_channel(CARRY_LOG_CHANNEL_ID)
    if channel is None:
        await interaction.response.send_message(
            "Couldn't find the carry log channel. Check the channel ID in the bot config.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    count = await count_marked_messages(CARRY_LOG_CHANNEL_ID, target.id, discord.Color.orange())

    await interaction.followup.send(
        f"{target.mention} has logged **{count}** {pluralize_carry(count)}.", ephemeral=True
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

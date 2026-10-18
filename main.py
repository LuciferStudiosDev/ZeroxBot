from __future__ import annotations

import json
import logging
import os
import secrets
import string
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

import database as db
from ptero_api import PteroAPIError, PteroClient

load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(),
                    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("zerox-ptero")

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
PANEL_URL = os.getenv("PTERO_PANEL_URL", "").strip().rstrip("/")
CLIENT_KEY = os.getenv("PTERO_CLIENT_API_KEY", "").strip()
APP_KEY = os.getenv("PTERO_APPLICATION_API_KEY", "").strip()
GUILD_ID = os.getenv("DISCORD_GUILD_ID", "").strip()
ALLOWED_ROLE_IDS = {int(x.strip()) for x in os.getenv("ALLOWED_ROLE_IDS", "").split(",") if x.strip().isdigit()}
OWNER_IDS = {int(x.strip()) for x in os.getenv("OWNER_USER_IDS", "").split(",") if x.strip().isdigit()}
AUDIT_CHANNEL_ID = os.getenv("AUDIT_CHANNEL_ID", "").strip()
PLANS_PATH = Path("config/plans.json")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing. Copy .env.example to .env and configure it.")
if not PANEL_URL.startswith(("https://", "http://localhost", "http://127.0.0.1")):
    raise RuntimeError("PTERO_PANEL_URL must use HTTPS (HTTP localhost is allowed for local testing).")

client_api = PteroClient(PANEL_URL, CLIENT_KEY, "client")
app_api = PteroClient(PANEL_URL, APP_KEY, "application")
intents = discord.Intents.default()

class ManagerBot(commands.Bot):
    async def setup_hook(self):
        db.init_db()
        await client_api.start()
        await app_api.start()
        try:
            if GUILD_ID.isdigit():
                guild = discord.Object(id=int(GUILD_ID))
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                log.info("Synced %d command(s) to guild %s", len(synced), GUILD_ID)
            else:
                synced = await self.tree.sync()
                log.info("Synced %d global command(s)", len(synced))
        except Exception:
            log.exception("Command sync failed")

    async def close(self):
        await client_api.close()
        await app_api.close()
        await super().close()

bot = ManagerBot(command_prefix="!", intents=intents)

def is_owner(interaction: discord.Interaction) -> bool:
    return interaction.user.id in OWNER_IDS or interaction.user.id == interaction.guild.owner_id if interaction.guild else interaction.user.id in OWNER_IDS

def can_manage(interaction: discord.Interaction) -> bool:
    if is_owner(interaction):
        return True
    if not isinstance(interaction.user, discord.Member):
        return False
    if ALLOWED_ROLE_IDS:
        return any(role.id in ALLOWED_ROLE_IDS for role in interaction.user.roles)
    return interaction.user.guild_permissions.administrator

async def guard(interaction: discord.Interaction, owner_only=False) -> bool:
    allowed = is_owner(interaction) if owner_only else can_manage(interaction)
    if not allowed:
        msg = "This command is restricted to the configured bot owner." if owner_only else "You need a configured manager role or Discord Administrator permission."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    return allowed

async def report_error(interaction: discord.Interaction, exc: Exception):
    log.warning("Command failed: %s", str(exc))
    message = str(exc) if isinstance(exc, PteroAPIError) else "Unexpected error. Check the bot logs."
    if interaction.response.is_done():
        await interaction.followup.send(f"⚠️ {message}", ephemeral=True)
    else:
        await interaction.response.send_message(f"⚠️ {message}", ephemeral=True)

async def audit(interaction, text):
    if AUDIT_CHANNEL_ID.isdigit():
        channel = bot.get_channel(int(AUDIT_CHANNEL_ID))
        if channel:
            try:
                await channel.send(f"**Audit** · {interaction.user} (`{interaction.user.id}`): {text}")
            except discord.HTTPException:
                pass

def plans():
    try:
        return json.loads(PLANS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

def attrs(entry):
    return entry.get("attributes", entry) if isinstance(entry, dict) else {}

def byte_size(value):
    value = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"

async def get_server(identifier, api=client_api):
    server = await api.find_server(identifier)
    return server

@bot.event
async def on_ready():
    log.info("Logged in as %s (%s)", bot.user, bot.user.id if bot.user else "?")

@bot.tree.command(name="about", description="About the ZERØX Ptero Manager bot")
async def about(interaction: discord.Interaction):
    embed = discord.Embed(title="ZERØX Ptero Manager", description="Discord management interface for Pterodactyl.", color=discord.Color.blurple())
    embed.add_field(name="API", value="Pterodactyl Client API + optional Application API", inline=False)
    embed.add_field(name="Security", value="Role checks, owner-only controls, ephemeral replies, environment secrets.", inline=False)
    embed.set_footer(text="Configure .env and config/plans.json before production use.")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="link", description="Link your Discord account to a Pterodactyl user ID")
@app_commands.describe(pterodactyl_user_id="Numeric user ID from the panel")
async def link(interaction: discord.Interaction, pterodactyl_user_id: int):
    if pterodactyl_user_id <= 0:
        return await interaction.response.send_message("Enter a valid positive panel user ID.", ephemeral=True)
    db.link_user(interaction.user.id, pterodactyl_user_id)
    await interaction.response.send_message(f"Linked your Discord account to Pterodactyl user ID `{pterodactyl_user_id}`.", ephemeral=True)

@bot.tree.command(name="unlink", description="Remove your Discord-to-Pterodactyl account link")
async def unlink(interaction: discord.Interaction):
    db.unlink_user(interaction.user.id)
    await interaction.response.send_message("Your account link was removed.", ephemeral=True)

admin = app_commands.Group(name="admin", description="Privileged panel administration")
@admin.command(name="list", description="List panel users (Application API)")
async def admin_list(interaction: discord.Interaction):
    if not await guard(interaction): return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        users = await app_api.app_list_users()
        lines = [f"• `{u.get('id')}` — {u.get('username')} ({u.get('email')})" for u in users[:30]]
        await interaction.followup.send("**Panel users**\n" + ("\n".join(lines) if lines else "No users returned."), ephemeral=True)
    except Exception as e: await report_error(interaction, e)

@admin.command(name="createuser", description="Create a Pterodactyl panel user")
@app_commands.describe(email="User email", username="Username", first_name="First name", last_name="Last name")
async def admin_createuser(interaction: discord.Interaction, email: str, username: str, first_name: str, last_name: str):
    if not await guard(interaction, owner_only=True): return
    password = "".join(secrets.choice(string.ascii_letters + string.digits + "!@#") for _ in range(18))
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        user = await app_api.app_create_user(email, username, first_name, last_name, password)
        await interaction.followup.send(
            f"Created user `{user.get('id', 'unknown')}`. Temporary password: ||`{password}`||\n"
            "Share it privately and ask the user to change it. This message is ephemeral.", ephemeral=True)
        await audit(interaction, f"created panel user {user.get('id')}")
    except Exception as e: await report_error(interaction, e)

@admin.command(name="console", description="Send a console command to a server")
@app_commands.describe(server_identifier="Server identifier", command="Console command to send")
async def admin_console(interaction: discord.Interaction, server_identifier: str, command: str):
    if not await guard(interaction): return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        server = await get_server(server_identifier)
        await client_api.send_command(str(server["identifier"]), command)
        await interaction.followup.send(f"Sent command to **{server.get('name', server_identifier)}**.", ephemeral=True)
        await audit(interaction, f"sent console command to {server.get('identifier')}")
    except Exception as e: await report_error(interaction, e)

@admin.command(name="manage", description="Show basic details for a panel server")
@app_commands.describe(server_identifier="Server identifier")
async def admin_manage(interaction: discord.Interaction, server_identifier: str):
    await manage_server.callback(interaction, server_identifier)

@admin.command(name="rename", description="Rename a server")
@app_commands.describe(server_identifier="Server identifier", new_name="New server name")
async def admin_rename(interaction: discord.Interaction, server_identifier: str, new_name: str):
    await rename_server.callback(interaction, server_identifier, new_name)

bot.tree.add_command(admin)

@bot.tree.command(name="list", description="List servers accessible through the configured Client API")
async def list_servers(interaction: discord.Interaction):
    if not await guard(interaction): return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        servers = await client_api.list_servers()
        if not servers:
            return await interaction.followup.send("No servers found for the configured Client API account.", ephemeral=True)
        embed = discord.Embed(title="ZERØX • Servers", color=discord.Color.blurple())
        for s in servers[:25]:
            embed.add_field(name=str(s.get("name", "Unnamed"))[:256],
                            value=f"Identifier: `{s.get('identifier', 'n/a')}`\nID: `{s.get('id', 'n/a')}`\nStatus: `{s.get('status') or 'unknown'}`",
                            inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e: await report_error(interaction, e)

@bot.tree.command(name="manage", description="Show server state and resource usage")
@app_commands.describe(server_identifier="Identifier shown by /list")
async def manage_server(interaction: discord.Interaction, server_identifier: str):
    if not await guard(interaction): return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        server = await get_server(server_identifier)
        res = await client_api.resources(str(server["identifier"]))
        resources = res.get("resources", {})
        limits = server.get("limits", {})
        embed = discord.Embed(title=f"Server • {server.get('name', server_identifier)}",
                              color=discord.Color.green() if res.get("current_state") == "running" else discord.Color.orange())
        embed.add_field(name="State", value=str(res.get("current_state", "unknown")), inline=True)
        embed.add_field(name="Memory", value=f"{byte_size(resources.get('memory_bytes'))} / {limits.get('memory', 'N/A')} MB", inline=True)
        embed.add_field(name="CPU", value=f"{resources.get('cpu_absolute', 0):.1f}% / {limits.get('cpu', 'N/A')}%", inline=True)
        embed.add_field(name="Disk", value=f"{byte_size(resources.get('disk_bytes'))} / {limits.get('disk', 'N/A')} MB", inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e: await report_error(interaction, e)

@bot.tree.command(name="power", description="Start, stop, or restart a server")
@app_commands.describe(server_identifier="Server identifier", action="Power action")
@app_commands.choices(action=[app_commands.Choice(name="Start", value="start"), app_commands.Choice(name="Stop", value="stop"), app_commands.Choice(name="Restart", value="restart")])
async def power(interaction: discord.Interaction, server_identifier: str, action: app_commands.Choice[str]):
    if not await guard(interaction): return
    if action.value == "stop":
        # Pterodactyl's stop signal is graceful; it may be rejected if server is already stopped.
        pass
    await interaction.response.send_message(
        f"Confirm **{action.name}** for `{server_identifier}`?",
        ephemeral=True,
        view=ConfirmPowerView(server_identifier, action.value, interaction.user.id)
    )

class ConfirmPowerView(discord.ui.View):
    def __init__(self, identifier, action, user_id):
        super().__init__(timeout=30)
        self.identifier, self.action, self.user_id = identifier, action, user_id

    @discord.ui.button(label="Confirm action", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Only the person who requested this action can confirm it.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            server = await get_server(self.identifier)
            await client_api.power(str(server["identifier"]), self.action)
            await interaction.followup.send(f"Sent **{self.action}** to **{server.get('name', self.identifier)}**.", ephemeral=True)
            await audit(interaction, f"power {self.action} on {server.get('identifier')}")
        except Exception as e: await report_error(interaction, e)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Only the requester can cancel this action.", ephemeral=True)
        await interaction.response.edit_message(content="Cancelled.", view=None)
        self.stop()

@bot.tree.command(name="console", description="Send a console command to a server")
@app_commands.describe(server_identifier="Server identifier", command="Command to send (not an interactive terminal)")
async def console(interaction: discord.Interaction, server_identifier: str, command: str):
    if not await guard(interaction): return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        server = await get_server(server_identifier)
        await client_api.send_command(str(server["identifier"]), command)
        await interaction.followup.send(f"Sent console command to **{server.get('name', server_identifier)}**.", ephemeral=True)
        await audit(interaction, f"console command sent to {server.get('identifier')}")
    except Exception as e: await report_error(interaction, e)

@bot.tree.command(name="rename", description="Rename a server")
@app_commands.describe(server_identifier="Server identifier", new_name="New server name")
async def rename_server(interaction: discord.Interaction, server_identifier: str, new_name: str):
    if not await guard(interaction): return
    if not new_name.strip(): return await interaction.response.send_message("Name cannot be empty.", ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        server = await get_server(server_identifier)
        await client_api.rename_client(str(server["identifier"]), new_name.strip())
        await interaction.followup.send(f"Renamed server to **{new_name[:100]}**.", ephemeral=True)
        await audit(interaction, f"renamed server {server.get('identifier')}")
    except Exception as e: await report_error(interaction, e)

@bot.tree.command(name="reinstall", description="Request a server reinstall (destructive)")
@app_commands.describe(server_identifier="Server identifier", confirm="Type YES to confirm")
async def reinstall(interaction: discord.Interaction, server_identifier: str, confirm: str):
    if not await guard(interaction, owner_only=True): return
    if confirm != "YES": return await interaction.response.send_message("Cancelled. Set confirm to `YES` to proceed.", ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        server = await get_server(server_identifier)
        await client_api.reinstall(str(server["identifier"]))
        await interaction.followup.send("Reinstall request sent. This may overwrite server files.", ephemeral=True)
        await audit(interaction, f"reinstall requested for {server.get('identifier')}")
    except Exception as e: await report_error(interaction, e)

@bot.tree.command(name="nodes", description="List Pterodactyl nodes (Application API)")
async def nodes(interaction: discord.Interaction):
    if not await guard(interaction): return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        items = await app_api.app_list_nodes()
        lines = [f"• `{n.get('id')}` **{n.get('name')}** — {n.get('fqdn')} ({n.get('memory', '?')} MB RAM)" for n in items[:30]]
        await interaction.followup.send("**Panel nodes**\n" + ("\n".join(lines) if lines else "No nodes returned."), ephemeral=True)
    except Exception as e: await report_error(interaction, e)

@bot.tree.command(name="delete", description="Delete a server using Application API (destructive)")
@app_commands.describe(server_identifier="Server identifier", confirm="Type DELETE to confirm")
async def delete_server(interaction: discord.Interaction, server_identifier: str, confirm: str):
    if not await guard(interaction, owner_only=True): return
    if confirm != "DELETE": return await interaction.response.send_message("Cancelled. Set confirm to `DELETE` to proceed.", ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        # Application API lookup is by numeric server ID; resolve through client list first.
        server = await get_server(server_identifier)
        await app_api.app_delete_server(int(server["id"]))
        await interaction.followup.send(f"Delete request sent for **{server.get('name', server_identifier)}**.", ephemeral=True)
        await audit(interaction, f"deleted server {server.get('id')}")
    except Exception as e: await report_error(interaction, e)

@bot.tree.command(name="suspend", description="Suspend a server (Application API)")
@app_commands.describe(server_identifier="Server identifier", confirm="Type SUSPEND to confirm")
async def suspend(interaction: discord.Interaction, server_identifier: str, confirm: str):
    if not await guard(interaction, owner_only=True): return
    if confirm != "SUSPEND": return await interaction.response.send_message("Cancelled.", ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        server = await get_server(server_identifier)
        await app_api.app_suspend_server(int(server["id"]))
        db.set_server_flag(str(server["identifier"]), "suspended", 1)
        await interaction.followup.send(f"Suspended **{server.get('name', server_identifier)}**.", ephemeral=True)
        await audit(interaction, f"suspended server {server.get('id')}")
    except Exception as e: await report_error(interaction, e)

@bot.tree.command(name="unsuspend", description="Unsuspend a server (Application API)")
@app_commands.describe(server_identifier="Server identifier")
async def unsuspend(interaction: discord.Interaction, server_identifier: str):
    if not await guard(interaction, owner_only=True): return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        server = await get_server(server_identifier)
        await app_api.app_unsuspend_server(int(server["id"]))
        db.set_server_flag(str(server["identifier"]), "suspended", 0)
        await interaction.followup.send(f"Unsuspended **{server.get('name', server_identifier)}**.", ephemeral=True)
        await audit(interaction, f"unsuspended server {server.get('id')}")
    except Exception as e: await report_error(interaction, e)

@bot.tree.command(name="purge", description="Delete messages from the current channel (owner only)")
@app_commands.describe(amount="Number of recent messages to delete, 1-100")
async def purge(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]):
    if not await guard(interaction, owner_only=True): return
    if not isinstance(interaction.channel, discord.TextChannel):
        return await interaction.response.send_message("Use this in a text channel.", ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.followup.send(f"Deleted {len(deleted)} message(s).", ephemeral=True)
        await audit(interaction, f"purged {len(deleted)} messages in channel {interaction.channel.id}")
    except Exception as e: await report_error(interaction, e)

@bot.tree.command(name="stopall", description="Stop all accessible servers (owner only, destructive)")
@app_commands.describe(confirm="Type STOP ALL to confirm")
async def stopall(interaction: discord.Interaction, confirm: str):
    if not await guard(interaction, owner_only=True): return
    if confirm != "STOP ALL": return await interaction.response.send_message("Cancelled.", ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        servers = await client_api.list_servers()
        successes, failures = 0, []
        for server in servers:
            try:
                await client_api.power(str(server.get("identifier")), "stop")
                successes += 1
            except Exception:
                failures.append(str(server.get("name", server.get("identifier", "?"))))
        await interaction.followup.send(f"Stop requests sent: **{successes}**. Failed: **{len(failures)}**" +
                                        (f"\nFailed servers: {', '.join(failures[:10])}" if failures else ""), ephemeral=True)
        await audit(interaction, f"stopall requested; successes={successes}, failures={len(failures)}")
    except Exception as e: await report_error(interaction, e)

@bot.tree.command(name="whitelist", description="Manage a Discord user whitelist")
@app_commands.describe(action="add, remove, or list", member="Member for add/remove", note="Optional note")
@app_commands.choices(action=[app_commands.Choice(name="Add", value="add"), app_commands.Choice(name="Remove", value="remove"), app_commands.Choice(name="List", value="list")])
async def whitelist(interaction: discord.Interaction, action: app_commands.Choice[str], member: discord.Member = None, note: str = ""):
    if not await guard(interaction): return
    if action.value == "add":
        if not member: return await interaction.response.send_message("Choose a member to add.", ephemeral=True)
        db.whitelist_add(member.id, note, interaction.user.id)
        msg = f"Added {member.mention} to the whitelist."
    elif action.value == "remove":
        if not member: return await interaction.response.send_message("Choose a member to remove.", ephemeral=True)
        db.whitelist_remove(member.id)
        msg = f"Removed {member.mention} from the whitelist."
    else:
        entries = db.whitelist_list()
        msg = "**Whitelist**\n" + ("\n".join(f"• <@{x['discord_user_id']}> — {x['note'] or 'no note'}" for x in entries[:40]) or "No entries.")
    await interaction.response.send_message(msg, ephemeral=True)

@bot.tree.command(name="bill", description="Create a local billing record (no payment is collected)")
@app_commands.describe(server_identifier="Server identifier", amount="Amount label, e.g. INR 499/month")
async def bill(interaction: discord.Interaction, server_identifier: str, amount: str):
    if not await guard(interaction): return
    db.add_bill(interaction.user.id, server_identifier, amount[:80])
    await interaction.response.send_message(f"Saved a local billing record for `{server_identifier}`: **{amount[:80]}**. No payment was collected.", ephemeral=True)

@bot.tree.command(name="server-expirations", description="Show locally tracked server expiration records")
async def server_expirations(interaction: discord.Interaction):
    if not await guard(interaction): return
    # Expiry records are populated when provisioning/renewal integration is configured.
    await interaction.response.send_message("Expiration tracking storage is ready, but this starter does not yet auto-populate expiry dates. Configure a billing/renewal workflow before relying on it.", ephemeral=True)

@bot.tree.command(name="renew", description="Record a renewal request (no payment processing)")
@app_commands.describe(server_identifier="Server identifier", days="Days to extend", payment_reference="Optional payment/reference ID")
async def renew(interaction: discord.Interaction, server_identifier: str, days: app_commands.Range[int, 1, 365], payment_reference: str = ""):
    if not await guard(interaction): return
    await interaction.response.send_message(
        f"Renewal request recorded for `{server_identifier}` for {days} day(s). "
        f"Reference: `{payment_reference[:80] or 'not provided'}`. No payment was verified and expiration was not changed.",
        ephemeral=True)

@bot.tree.command(name="autobackup-enable", description="Enable/disable a local automatic-backup flag")
@app_commands.describe(server_identifier="Server identifier", enabled="Enable or disable the flag")
async def autobackup_enable(interaction: discord.Interaction, server_identifier: str, enabled: bool):
    if not await guard(interaction): return
    try:
        server = await get_server(server_identifier)
        db.save_server_record(str(server["identifier"]))
        db.set_server_flag(str(server["identifier"]), "autobackup", int(enabled))
        await interaction.response.send_message(
            f"Automatic-backup flag set to **{enabled}** for `{server.get('name', server_identifier)}`. "
            "A real backup scheduler/retention policy still needs to be configured.", ephemeral=True)
    except Exception as e: await report_error(interaction, e)

@bot.tree.command(name="schedule-restart", description="Save a restart schedule description")
@app_commands.describe(server_identifier="Server identifier", schedule="Schedule in UTC, e.g. daily at 04:00 UTC")
async def schedule_restart(interaction: discord.Interaction, server_identifier: str, schedule: str):
    if not await guard(interaction): return
    try:
        server = await get_server(server_identifier)
        db.add_schedule(str(server["identifier"]), "restart", schedule, interaction.user.id)
        await interaction.response.send_message(
            f"Saved restart schedule for **{server.get('name', server_identifier)}**: `{schedule[:120]}`. "
            "This stores the schedule only; a scheduler worker must be running to execute it.", ephemeral=True)
    except Exception as e: await report_error(interaction, e)

@bot.tree.command(name="autosuspend", description="Record an auto-suspend policy for a server")
@app_commands.describe(server_identifier="Server identifier", days_overdue="Days overdue before suspension")
async def autosuspend(interaction: discord.Interaction, server_identifier: str, days_overdue: app_commands.Range[int, 1, 90]):
    if not await guard(interaction, owner_only=True): return
    try:
        server = await get_server(server_identifier)
        db.save_server_record(str(server["identifier"]))
        await interaction.response.send_message(
            f"Policy noted for `{server.get('identifier')}`: suspend after {days_overdue} overdue day(s). "
            "A billing/expiry worker must be configured to enforce this policy.", ephemeral=True)
    except Exception as e: await report_error(interaction, e)

@bot.tree.command(name="deletesuspended", description="Delete a suspended server (owner only)")
@app_commands.describe(server_identifier="Server identifier", confirm="Type DELETE SUSPENDED to confirm")
async def deletesuspended(interaction: discord.Interaction, server_identifier: str, confirm: str):
    if not await guard(interaction, owner_only=True): return
    if confirm != "DELETE SUSPENDED": return await interaction.response.send_message("Cancelled.", ephemeral=True)
    try:
        server = await get_server(server_identifier)
        record = db.get_server_record(str(server["identifier"]))
        if not record or not record.get("suspended"):
            return await interaction.response.send_message("This bot has no local record that this server is suspended. Refusing to delete.", ephemeral=True)
        await app_api.app_delete_server(int(server["id"]))
        await interaction.response.send_message(f"Delete request sent for `{server.get('name')}`.", ephemeral=True)
    except Exception as e: await report_error(interaction, e)

@bot.tree.command(name="create-free", description="Provision a server using the configured free plan")
@app_commands.describe(name="Server name", owner_email="Email of existing Pterodactyl user", nest_id="Nest ID", egg_id="Egg ID", allocation_id="Allocation ID")
async def create_free(interaction: discord.Interaction, name: str, owner_email: str, nest_id: int, egg_id: int, allocation_id: int):
    await provision_server(interaction, "free", name, owner_email, nest_id, egg_id, allocation_id)

@bot.tree.command(name="create-paid", description="Provision a server using the configured paid plan")
@app_commands.describe(name="Server name", owner_email="Email of existing Pterodactyl user", nest_id="Nest ID", egg_id="Egg ID", allocation_id="Allocation ID")
async def create_paid(interaction: discord.Interaction, name: str, owner_email: str, nest_id: int, egg_id: int, allocation_id: int):
    await provision_server(interaction, "paid", name, owner_email, nest_id, egg_id, allocation_id)

async def provision_server(interaction, plan_name, name, owner_email, nest_id, egg_id, allocation_id):
    if not await guard(interaction, owner_only=True): return
    config = plans().get(plan_name)
    if not config:
        return await interaction.response.send_message(f"Plan `{plan_name}` is missing in config/plans.json.", ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        users = await app_api.pages("users", {"filter[email]": owner_email})
        user = next((attrs(u) for u in users if attrs(u).get("email", "").casefold() == owner_email.casefold()), None)
        if not user:
            return await interaction.followup.send("No matching Pterodactyl user found. Create the user first with `/admin createuser`.", ephemeral=True)
        payload = {
            "name": name[:100],
            "user": int(user["id"]),
            "egg": int(egg_id),
            "docker_image": "ghcr.io/parkervcp/yolks:ubuntu",
            "startup": "bash",
            "environment": {},
            "limits": {k: int(config[k]) for k in ("memory", "swap", "disk", "io", "cpu")},
            "feature_limits": {k: int(config[k]) for k in ("databases", "allocations", "backups")},
            "deploy": {"locations": [int(os.getenv("DEFAULT_LOCATION_ID", "0"))] if os.getenv("DEFAULT_LOCATION_ID", "").isdigit() else [], "dedicated_ip": False, "port_range": [str(allocation_id)]},
            "start_on_completion": False
        }
        created = await app_api.app_create_server(payload)
        identifier = str(created.get("identifier", created.get("id", "")))
        db.save_server_record(identifier, interaction.user.id, plan_name)
        await interaction.followup.send(f"Created server **{created.get('name', name)}** (`{identifier}`). Check panel allocations and egg startup settings.", ephemeral=True)
        await audit(interaction, f"provisioned {plan_name} server {identifier}")
    except Exception as e: await report_error(interaction, e)

@bot.tree.command(name="change-egg", description="Show guidance for changing a server egg")
@app_commands.describe(server_identifier="Server identifier", egg_id="Target egg ID")
async def change_egg(interaction: discord.Interaction, server_identifier: str, egg_id: int):
    if not await guard(interaction, owner_only=True): return
    await interaction.response.send_message(
        f"Egg change requested for `{server_identifier}` to egg ID `{egg_id}`. This action is not implemented automatically because egg changes require a validated startup/environment mapping; use the panel or add a tested migration adapter.",
        ephemeral=True)

@bot.tree.command(name="resize", description="Show guidance for resizing server resource limits")
@app_commands.describe(server_identifier="Server identifier", memory_mb="Memory in MB", disk_mb="Disk in MB", cpu_percent="CPU limit percent")
async def resize(interaction: discord.Interaction, server_identifier: str, memory_mb: app_commands.Range[int, 128, 1048576], disk_mb: app_commands.Range[int, 128, 1048576], cpu_percent: app_commands.Range[int, 1, 10000]):
    if not await guard(interaction, owner_only=True): return
    await interaction.response.send_message(
        f"Resize request prepared for `{server_identifier}`: RAM {memory_mb} MB, disk {disk_mb} MB, CPU {cpu_percent}%. "
        "This starter does not apply limit changes automatically; use the panel or implement a validated Application API update for your panel version.",
        ephemeral=True)

@bot.tree.command(name="create-free-help", description="Explain free plan provisioning requirements")
async def create_free_help(interaction: discord.Interaction):
    await interaction.response.send_message("Use `/create-free` with a configured free plan, existing panel user, nest, egg, and available allocation.", ephemeral=True)

# Whitelist and account-link state are local to this bot. Additional panel-specific operations are intentionally not faked.
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    log.exception("Unhandled application command error", exc_info=error)
    msg = "The command failed unexpectedly. Check the bot logs."
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)

if __name__ == "__main__":
    bot.run(TOKEN, log_handler=None)

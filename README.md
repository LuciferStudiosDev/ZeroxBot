# ZERØX Ptero Manager Bot

A Python Discord slash-command bot project for managing a Pterodactyl panel. Designed for local testing and Android Termux.

## Included command surface

- `/about`, `/link`, `/unlink`
- `/admin console`, `/admin createuser`, `/admin list`, `/admin manage`, `/admin rename`
- `/autobackup-enable`, `/autosuspend`, `/bill`
- `/change-egg`, `/console`, `/create-free`, `/create-paid`
- `/delete`, `/deletesuspended`, `/list`, `/manage`, `/nodes`, `/power`
- `/purge`, `/reinstall`, `/rename`, `/renew`, `/resize`
- `/schedule-restart`, `/server-expirations`, `/stopall`
- `/suspend`, `/unsuspend`, `/whitelist`

Some operations are implemented using Pterodactyl API endpoints. Advanced billing, automated backups, scheduled restarts, free/paid provisioning, console streaming, and custom plan/egg policies require panel-specific configuration or additional infrastructure. Those commands report their current configuration status rather than pretending an operation succeeded.

## Important API distinction

- **Client API key**: manages servers the account already has access to.
- **Application API key**: powerful panel-wide administrative access. Required for user creation, nodes/eggs, server provisioning, suspending, deleting and other admin actions.
- Use a dedicated Pterodactyl account and the narrowest permissions possible. Do not use an owner API key unless absolutely necessary.
- This bot does not collect or store API keys in its database. Secrets belong in `.env`.

## Install (Windows/Linux/macOS)

Python 3.10+ recommended.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`, then run:

```bash
python main.py
```

Set `DISCORD_GUILD_ID` to your Discord test server ID for fast slash-command sync. Leave it empty for global command registration (which can take time to appear).

## Install on Android (Termux)

1. Install Termux from F-Droid or the official Termux GitHub releases.
2. Run:

```bash
pkg update && pkg upgrade
pkg install python git
# Transfer and unzip this ZIP, then cd into the extracted folder
pip install -r requirements.txt
cp .env.example .env
nano .env
python main.py
```

Keep Termux running and disable battery optimization for Termux if Android kills background processes. A phone is not a dependable 24/7 host.

## Setup checklist

1. Create a Discord application and bot at https://discord.com/developers/applications.
2. Invite it with `bot` and `applications.commands` scopes. Avoid granting Discord Administrator to the bot.
3. Add your Pterodactyl panel URL.
4. Create API credentials in the panel. Configure Client API and Application API keys separately.
5. Set a manager role ID or leave `ALLOWED_ROLE_IDS` blank to require Discord Administrator permission.
6. Set `OWNER_USER_IDS` for owner-only commands such as `/stopall` and `/purge`.
7. Configure product plans and limits in `config/plans.json`.
8. Test with a non-critical test server first.

## Command safety

- Server identifiers are resolved from the API list before use; arbitrary paths are not accepted.
- Power, delete, reinstall, suspend, and stop-all actions require authorization.
- Destructive commands ask for a confirmation parameter where appropriate.
- `/console` is a basic command sender if the panel supports the Client API command endpoint; it is not an interactive live terminal stream.
- `/autobackup-enable`, `/schedule-restart`, `/autosuspend`, `/bill`, `/renew`, and expiration tracking need an external scheduler/payment integration for production automation. This starter uses SQLite to store settings/records but does not charge customers or run payment processing.
- `/create-free` and `/create-paid` require valid plan definitions and Application API permissions.

## Files

- `main.py` — Discord commands and authorization
- `ptero_api.py` — Pterodactyl API clients
- `database.py` — SQLite links, whitelist and server records
- `config/plans.json` — example free/paid plan limits
- `.env.example` — configuration template

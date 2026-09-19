"""Read-only: ask Discord which slash commands are actually registered.

Answers "did my new commands make it?" without waiting for client propagation
or guessing from logs. Logs in over HTTP only (no gateway connection, so it does
NOT disturb the running bot service), fetches the registered command list, and
exits. It never adds, edits, or removes anything.

Usage:
    python check_commands.py                 # global commands only
    python check_commands.py <GUILD_ID>      # also list that guild's commands

Read the output like this:
  - Command listed under "global"  -> registered with Discord. If your Discord
    client still does not show it, that is client-side propagation (up to ~1
    hour). Press Ctrl+R in Discord to force a refresh, or use sync_guild.py to
    register an instant guild copy.
  - Command missing entirely       -> the running bot's tree.sync() did not
    include it. Check the Railway deploy logs for "Slash commands synced
    successfully" (or a sync failure).
"""

import asyncio
import os
import sys

from dotenv import load_dotenv

# Must run before importing app: app.py reads DISCORD_BOT_TOKEN and
# DEEPSEEK_API_KEY at module level and raises if either is missing.
load_dotenv()

# Check the secrets BEFORE importing app, because a missing one makes the import
# itself raise a bare ValueError. Checking first turns that traceback into a
# message that says what to do.
_missing = [name for name in ("DISCORD_BOT_TOKEN", "DEEPSEEK_API_KEY") if not os.getenv(name)]
if _missing:
    raise SystemExit(
        f"Missing: {', '.join(_missing)}\n\n"
        "Easiest fix: run this script ON the bot's server, where .env already exists:\n"
        "    ssh -i <key> ubuntu@<PUBLIC_IP>\n"
        "    cd Discord_Bot_Main && git pull && source venv/bin/activate\n"
        "    python check_commands.py <GUILD_ID>\n\n"
        "To run it locally instead, create a .env in this folder (it is gitignored):\n"
        "    DISCORD_BOT_TOKEN=<copy from the bot's host>\n"
        "    DEEPSEEK_API_KEY=<copy from the bot's host>\n"
        "Or pass the token inline:\n"
        "    DISCORD_BOT_TOKEN=xxx .venv/bin/python check_commands.py <GUILD_ID>"
    )

# app.py has an `if __name__ == "__main__"` guard, so this does NOT start the bot.
from app import bot

WATCHED = ("pdfs", "pdfexport", "pdflink")


def report(label, commands):
    """Print the names found, flagging the PDF commands we care about."""
    names = sorted(command.name for command in commands)
    print(f"{label} ({len(names)}):")
    if not names:
        print("  (none)")
    for name in names:
        marker = " <-- PDF" if name in WATCHED else ""
        print(f"  {name}{marker}")

    print()
    print("PDF index commands:")
    for name in WATCHED:
        found = "registered" if name in names else "NOT registered"
        print(f"  {found}: /{name}")
    return names


async def main(guild_id):
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit(
            "DISCORD_BOT_TOKEN not set. Add it to .env or export it in your shell.\n"
            "Copy the value from Railway -> your service -> Variables."
        )

    await bot.login(token)
    try:
        print("Global commands as Discord currently has them:\n")
        global_names = report("global", await bot.tree.fetch_commands())
        print()

        if guild_id is not None:
            guild = bot.get_guild(guild_id)
            if guild is None:
                raise SystemExit(
                    f"This bot is not in guild {guild_id}, so it has no commands there.\n"
                    "Double-check the ID (Developer Mode -> right-click server -> Copy Server ID)."
                )
            print(f"Guild-scoped commands for {guild.name}:\n")
            report("guild", await bot.tree.fetch_commands(guild=guild))
            print()

        missing = [name for name in WATCHED if name not in global_names]
        if not missing:
            print(
                "Verdict: all three PDF commands are registered with Discord.\n"
                "If your Discord client still does not show them, that is propagation.\n"
                "Press Ctrl+R in Discord, or register an instant guild copy with:\n"
                "  .venv/bin/python sync_guild.py <GUILD_ID> --commands pdfs,pdfexport,pdflink"
            )
        else:
            print(
                "Verdict: missing from Discord's global list: " + ", ".join(missing) + "\n"
                "That means the running bot has not synced them. Check the Railway deploy\n"
                "logs for 'Slash commands synced successfully' or a sync failure."
            )
    finally:
        await bot.close()


if __name__ == "__main__":
    if len(sys.argv) > 2:
        raise SystemExit("usage: python check_commands.py [GUILD_ID]")
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) == 2 else None))

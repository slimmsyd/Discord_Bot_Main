"""Register slash commands as INSTANT guild commands for one server.

Global slash commands can take up to ~1 hour to propagate to every server.
Guild-scoped commands appear instantly. This script logs in over HTTP only
(no gateway connection, so it does NOT disturb the running bot service),
registers the requested commands for the given guild, and exits.

Usage:
    python sync_guild.py <GUILD_ID>
        Register /listchannel (the default).

    python sync_guild.py <GUILD_ID> --commands pdfs,pdfexport,pdflink
        Register the PDF index commands instead.

    python sync_guild.py <GUILD_ID> --clear
        Remove this guild's guild-scoped copies, reverting to global-only.

Each named command can briefly appear TWICE in the target server: the instant
guild copy added here, plus the global copy once Discord propagates it. Run with
--clear afterwards to collapse back to a single global entry.

Only the named commands are touched: sync(guild=...) uploads just the
guild-scoped set, and the global sync in app.py's on_ready is unaffected.
"""

import asyncio
import os
import sys

import discord
from dotenv import load_dotenv

# Must run before importing app: app.py reads DISCORD_BOT_TOKEN and
# DEEPSEEK_API_KEY at module level and raises if either is missing.
load_dotenv()

# Importing app registers all @bot.tree.command definitions. app.py has an
# `if __name__ == "__main__"` guard, so this does NOT start the bot.
#
# Check the secrets BEFORE importing, because a missing one makes the import
# itself raise a bare ValueError instead of a message that says what to do.
_missing = [name for name in ("DISCORD_BOT_TOKEN", "DEEPSEEK_API_KEY") if not os.getenv(name)]
if _missing:
    raise SystemExit(
        f"Missing: {', '.join(_missing)}\n\n"
        "These are the same secrets the bot itself uses. Copy them from your host's\n"
        "settings (Railway: your service -> Variables tab).\n\n"
        "To set them up locally, this repo already ships a template:\n"
        "    cd /Users/sydneysanders/Desktop/Code_Projects/Discord_Bot_Main\n"
        "    cp .env.example .env && open -e .env\n"
        "Paste the two real values, save, and re-run this command.\n"
        "(.env is gitignored, so the secrets cannot be committed.)\n\n"
        "Already have the token? Pass both inline for one run:\n"
        "    DISCORD_BOT_TOKEN=xxx DEEPSEEK_API_KEY=yyy \\\n"
        "      .venv/bin/python sync_guild.py <GUILD_ID> --commands pdfs,pdfexport,pdflink"
    )

from app import bot

DEFAULT_COMMANDS = ("listchannel",)
COMMANDS_FLAG = "--commands"
CLEAR_FLAG = "--clear"


def parse_args(argv):
    """(guild_id, command names, clear) from raw argv, or SystemExit on misuse."""
    if len(argv) < 2 or argv[1].startswith("-"):
        raise SystemExit(
            "usage: python sync_guild.py <GUILD_ID> "
            f"[{COMMANDS_FLAG} name1,name2] [{CLEAR_FLAG}]"
        )

    names = list(DEFAULT_COMMANDS)
    if COMMANDS_FLAG in argv:
        try:
            raw = argv[argv.index(COMMANDS_FLAG) + 1]
        except IndexError:
            raise SystemExit(f"{COMMANDS_FLAG} needs a comma-separated list of names")
        names = [name.strip() for name in raw.split(",") if name.strip()]
        if not names:
            raise SystemExit(f"{COMMANDS_FLAG} needs at least one command name")

    return int(argv[1]), names, CLEAR_FLAG in argv


def resolve(names):
    """The tree's commands matching `names`, or SystemExit listing what exists."""
    available = {command.name: command for command in bot.tree.get_commands()}
    missing = [name for name in names if name not in available]
    if missing:
        raise SystemExit(
            f"Not in the command tree: {', '.join(missing)}\n"
            f"Available: {', '.join(sorted(available))}"
        )
    return [available[name] for name in names]


async def main(guild_id, names, clear):
    guild = discord.Object(id=guild_id)
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("DISCORD_BOT_TOKEN not set")

    if not clear:
        for command in resolve(names):
            bot.tree.add_command(command, guild=guild)

    await bot.login(token)
    synced = await bot.tree.sync(guild=guild)
    outcome = "cleared" if clear else "now"
    print(f"Guild {guild_id} guild-scoped commands {outcome}: {[c.name for c in synced]}")
    await bot.close()


if __name__ == "__main__":
    guild_id, commands, should_clear = parse_args(sys.argv)
    asyncio.run(main(guild_id, commands, should_clear))

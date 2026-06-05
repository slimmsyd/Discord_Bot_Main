"""One-off: register /listchannel as an INSTANT guild command for one server.

Global slash commands can take up to ~1 hour to propagate to every server.
Guild-scoped commands appear instantly. This script logs in over HTTP only
(no gateway connection, so it does NOT disturb the running bot service),
registers ONLY the /listchannel command for the given guild, and exits.

Usage:
    python sync_guild.py <GUILD_ID>            # add instant /listchannel to guild
    python sync_guild.py <GUILD_ID> --clear    # remove guild-scoped copies (revert
                                               # to global-only once it propagates)

Only /listchannel is touched, so at most that one command can briefly appear
twice in the target server (the instant guild copy + the global copy once it
lands). Run with --clear afterwards to collapse back to a single global entry.
"""

import asyncio
import os
import sys

import discord
from dotenv import load_dotenv

# Importing app registers all @bot.tree.command definitions. app.py has an
# `if __name__ == "__main__"` guard, so this import does NOT start the bot.
from app import bot

load_dotenv()


async def main(guild_id: int, clear: bool) -> None:
    guild = discord.Object(id=guild_id)
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("DISCORD_BOT_TOKEN not set")

    if not clear:
        cmd = bot.tree.get_command("listchannel")
        if cmd is None:
            raise SystemExit("listchannel command not found in tree")
        bot.tree.add_command(cmd, guild=guild)

    await bot.login(token)
    synced = await bot.tree.sync(guild=guild)
    print(f"Guild {guild_id} guild-scoped commands now:", [c.name for c in synced])
    await bot.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: python sync_guild.py <GUILD_ID> [--clear]")
    gid = int(sys.argv[1])
    do_clear = "--clear" in sys.argv[2:]
    asyncio.run(main(gid, do_clear))

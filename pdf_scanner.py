"""Async Discord I/O for the PDF index: scope resolution, scanning, caching.

Discord messages are the only source of truth. Nothing here writes to disk, so
a redeploy loses the cache and nothing else.
"""

import logging

logger = logging.getLogger("discord_bot")


def _can_view(channel, invoker):
    """True if `invoker` may view `channel`. Fails closed."""
    if invoker is None:
        return True
    try:
        return bool(channel.permissions_for(invoker).view_channel)
    except Exception:
        logger.warning("Could not check permissions for #%s", getattr(channel, "name", "?"))
        return False


def _threads_of(channel, invoker):
    """Active threads of a text channel, filtered to what `invoker` can view."""
    return [t for t in (getattr(channel, "threads", None) or []) if _can_view(t, invoker)]


def resolve_scope(guild, *, category=None, channel=None, invoker=None):
    """The channels to scan.

    An explicit `channel` wins over `category`; neither means the whole guild.
    Results are always filtered to channels `invoker` can actually view, so the
    index never leaks a link the caller could not open in Discord.
    """
    if channel is not None:
        if not _can_view(channel, invoker):
            raise PermissionError(f"You don't have access to #{channel.name}.")
        return [channel] + _threads_of(channel, invoker)

    if category is not None:
        candidates = list(category.text_channels)
    else:
        candidates = list(guild.text_channels)

    expanded = []
    for candidate in candidates:
        if not _can_view(candidate, invoker):
            continue
        expanded.append(candidate)
        expanded.extend(_threads_of(candidate, invoker))
    return expanded

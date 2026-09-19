"""Async Discord I/O for the PDF index: scope resolution, scanning, caching.

Discord messages are the only source of truth. Nothing here writes to disk, so
a redeploy loses the cache and nothing else.
"""

import logging
import time
from dataclasses import dataclass, field, replace

import discord

from pdf_index import annotate_duplicates, collect_pdf_records

logger = logging.getLogger("discord_bot")


def _can_view(channel, invoker):
    """True if `invoker` may view `channel`. Fails closed.

    An absent invoker cannot be proven to have access, so we refuse rather than
    hand back an unfiltered index. Every real caller passes `invoker=interaction.user`.
    """
    if invoker is None:
        return False
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


@dataclass
class ScanResult:
    """Everything one scan produced: the PDF records, plus channels we could not read."""

    records: list = field(default_factory=list)
    skipped: list = field(default_factory=list)


async def scan_channels(channels, *, on_progress=None):
    """Scan `channels` for PDF attachments, never letting one channel abort the run.

    `on_progress(done, total)` is awaited after each channel so the command can
    edit its "scanning…" message.
    """
    result = ScanResult()
    total = len(channels)

    for index, channel in enumerate(channels, start=1):
        name = getattr(channel, "name", "?")
        try:
            async for message in channel.history(limit=None):
                result.records.extend(collect_pdf_records([message], channel))
        except discord.Forbidden:
            logger.warning("Skipping #%s: missing Read Message History permission", name)
            result.skipped.append(name)
        except discord.HTTPException as exc:
            logger.error("Discord error while scanning #%s: %s", name, exc)
            result.skipped.append(name)

        if on_progress is not None:
            await on_progress(index, total)

    annotate_duplicates(result.records)
    return result


CACHE_TTL = 600

_CACHE = {}


def clear_cache():
    """Drop every cached scan. Used by tests; harmless in production."""
    _CACHE.clear()


def _cache_key(channels):
    return tuple(sorted(str(channel.id) for channel in channels))


def _independent(result):
    """A copy of `result` the caller may freely mutate.

    The cache never shares its own object with a caller, so an in-place edit
    here (sorting, trimming, appending) can never poison what the next caller
    receives.
    """
    return replace(
        result,
        records=[dict(record) for record in result.records],
        skipped=list(result.skipped),
    )


async def scan_with_cache(channels, *, on_progress=None, ttl=CACHE_TTL, clock=time.monotonic):
    """`scan_channels`, memoised per channel-set for `ttl` seconds.

    The cache lives in memory only. Discord signed URLs stay valid for ~24h, so
    a 10-minute cache can never hand out an expired link.
    """
    key = _cache_key(channels)
    now = clock()
    cached = _CACHE.get(key)
    if cached is not None and now - cached[0] < ttl:
        return _independent(cached[1])

    result = await scan_channels(channels, on_progress=on_progress)
    _CACHE[key] = (now, _independent(result))
    return result

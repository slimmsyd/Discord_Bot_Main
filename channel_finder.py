"""Semantic + fuzzy channel matching for the /listchannel command.

Deliberately free of Discord and environment dependencies so the ranking logic
can be unit-tested with a stubbed DeepSeek client (no live bot/token needed).
"""

import json
import logging
from difflib import SequenceMatcher

logger = logging.getLogger('discord_bot')

# How many characters of a channel topic to include in the AI prompt.
TOPIC_TRUNCATE = 120


def _truncate(text, limit=TOPIC_TRUNCATE):
    if not text:
        return ""
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def fuzzy_match(name, channels, limit=2):
    """Local fallback: rank channels by literal name/topic similarity.

    channels: list of {"id": int, "name": str, "topic": str|None}
    Returns: list of {"id": int, "reason": str}, best first, up to `limit`.
    """
    query = name.lower().strip()
    scored = []
    for ch in channels:
        ch_name = (ch.get("name") or "").lower()
        ch_topic = (ch.get("topic") or "").lower()
        score = 0.0
        if query in ch_name:
            score += 2.0
        if query in ch_topic:
            score += 1.0
        # Catch typos / partial overlap on the name.
        score += SequenceMatcher(None, query, ch_name).ratio()
        # Discard low-confidence hits: a SequenceMatcher-only match must clear
        # 0.5 to count, while any literal substring match (>= 1.0) always passes.
        if score > 0.5:
            scored.append((score, ch))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [{"id": ch["id"], "reason": "name/topic match"} for _, ch in scored[:limit]]

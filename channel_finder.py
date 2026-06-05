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


def rank_channels(name, channels, client, model, limit=2):
    """AI ranking via a DeepSeek (OpenAI-compatible) client.

    Returns list of {"id": int, "reason": str} for the best matches, validated
    against the input channel IDs. Returns [] on any error or if nothing valid
    comes back, signalling the caller to fall back to fuzzy_match.
    """
    if not channels:
        return []
    valid_ids = {int(ch["id"]) for ch in channels}
    catalog = [
        {"id": ch["id"], "name": ch.get("name", ""), "topic": _truncate(ch.get("topic"))}
        for ch in channels
    ]
    system = (
        "You help members find the right Discord channel. Given a search term and "
        "a catalog of channels (id, name, topic), pick the channels whose PURPOSE "
        "best matches the term by meaning, not just literal wording. "
        f"Return at most {limit}, best first. "
        'Respond ONLY as JSON: {"matches": [{"id": <channel id>, "reason": "<short reason>"}]}. '
        "If nothing fits, return an empty matches list."
    )
    user = f'Search term: "{name}"\nChannels:\n{json.dumps(catalog)}'
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            max_tokens=300,
            temperature=0.2,
        )
        data = json.loads(response.choices[0].message.content)
    except Exception as e:
        logger.error(f"rank_channels AI call failed: {e}", exc_info=True)
        return []

    matches = data.get("matches", [])
    if not isinstance(matches, list):
        return []

    results = []
    seen = set()
    for match in matches:
        try:
            cid = int(match["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if cid in valid_ids and cid not in seen:
            seen.add(cid)
            reason = str(match.get("reason", "")).strip() or "relevant match"
            results.append({"id": cid, "reason": reason})
        if len(results) >= limit:
            break
    return results


def find_channels(name, channels, client, model, limit=2):
    """Try AI ranking; fall back to fuzzy matching if it yields nothing."""
    results = rank_channels(name, channels, client, model, limit=limit)
    if results:
        return results
    return fuzzy_match(name, channels, limit=limit)

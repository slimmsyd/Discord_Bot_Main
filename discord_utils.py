"""Small Discord helpers, kept free of bot/env dependencies so they're testable."""

# Discord rejects messages longer than 2000 characters. We split at 1900 to
# leave headroom for any prefix (e.g. the "🔮 " on Oracle replies).
DISCORD_CHUNK_LIMIT = 1900


def split_for_discord(text, limit=DISCORD_CHUNK_LIMIT):
    """Split text into chunks that fit under Discord's message length cap.

    Returns a list with at least one element; concatenating the chunks
    reproduces the original text exactly.
    """
    if len(text) <= limit:
        return [text]
    return [text[i:i + limit] for i in range(0, len(text), limit)]

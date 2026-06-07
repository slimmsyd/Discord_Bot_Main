"""In-memory, ephemeral rolling conversation buffer per (channel, user).

Intentionally NOT persisted: memory resets on restart, which avoids the
privacy/ToS weight of storing chat content. All time is INJECTED (callers pass
`now` = epoch seconds) so TTL and eviction are deterministic under test — there
are no wall-clock calls inside this module.
"""

DEFAULT_MAX_TURNS = 6       # ~3 user/assistant exchanges
DEFAULT_TTL_SECONDS = 1800  # 30-minute idle expiry


def make_key(channel_id, user_id):
    """Canonical store key for a (channel, user) pair."""
    return (channel_id, user_id)


class ConversationMemory:
    """Bounded, TTL-evicting per-key turn buffers held in memory."""

    def __init__(self, max_turns=DEFAULT_MAX_TURNS, ttl_seconds=DEFAULT_TTL_SECONDS):
        self.max_turns = max_turns
        self.ttl_seconds = ttl_seconds
        self._store = {}  # key -> {"turns": [ {"role","content"} ], "last": epoch}

    def get_history(self, key, now):
        """Return the turns for `key` (oldest first), or [] if absent/expired.
        Expiry on read also evicts the stale entry."""
        entry = self._store.get(key)
        if entry is None:
            return []
        if now - entry["last"] > self.ttl_seconds:
            del self._store[key]
            return []
        return entry["turns"]

    def append_turn(self, key, role, content, now):
        """Append one turn, refresh the entry's last-touched time, and trim to
        the most recent `max_turns` turns."""
        entry = self._store.get(key)
        if entry is None:
            entry = {"turns": [], "last": now}
            self._store[key] = entry
        entry["turns"].append({"role": role, "content": content})
        entry["turns"] = entry["turns"][-self.max_turns:]
        entry["last"] = now

    def prune(self, now):
        """Evict every key idle beyond ttl_seconds (bounds key growth)."""
        stale = [k for k, e in self._store.items() if now - e["last"] > self.ttl_seconds]
        for k in stale:
            del self._store[k]

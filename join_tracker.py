"""Invite-based join tracking.

Discord does NOT expose, after the fact, which invite a member used. The only
way to know "how they joined" is to snapshot each invite's use-count and, on a
new join, find which count went up by one. This module holds the pure diff
(`diff_invite_uses`, fully testable) and a small JSON-backed `JoinLog` that
persists what we learn so it survives bot restarts.

Members who joined before tracking started simply have no record — the export
shows them as method "unknown".
"""

import json
import os


def diff_invite_uses(before, after):
    """Return the invite code whose use-count increased between two snapshots.

    `before`/`after` are ``{code: uses}`` dicts. Returns the first code whose
    count grew (the invite that was just used), or None if it can't be told
    apart — e.g. a single-use invite that was consumed and auto-deleted, or a
    vanity-URL join, neither of which leaves a diff we can attribute.
    """
    for code, uses in after.items():
        if uses > before.get(code, 0):
            return code
    return None


class JoinLog:
    """Persisted ``user_id -> join record`` map, backed by a JSON file.

    Records are written atomically (temp file + os.replace) so a crash mid-write
    can't corrupt the log.
    """

    def __init__(self, path):
        self.path = path
        self._data = {}
        self.load()

    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                # A corrupt/unreadable log shouldn't take the bot down; start fresh.
                self._data = {}

    def save(self):
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)
        os.replace(tmp, self.path)

    def record(self, user_id, record):
        """Store (and persist) the join record for a user."""
        self._data[str(user_id)] = record
        self.save()

    def get(self, user_id):
        """Return the stored join record for a user, or None."""
        return self._data.get(str(user_id))

    def all_records(self):
        """Return every join record with its user_id merged in (the dict key)."""
        out = []
        for uid, rec in self._data.items():
            merged = dict(rec)
            merged.setdefault("user_id", uid)
            out.append(merged)
        return out


class LeaveLog:
    """Append-only JSON list of leave events: ``[{user_id, username, left_at, joined_at}]``.

    A list (not a dict) because the same user can leave, rejoin, and leave
    again — each departure is its own event worth counting.
    """

    def __init__(self, path):
        self.path = path
        self._events = []
        self.load()

    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self._events = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._events = []

    def save(self):
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._events, f, indent=2)
        os.replace(tmp, self.path)

    def record(self, event):
        """Append (and persist) one leave event."""
        self._events.append(event)
        self.save()

    def events(self):
        """Return all recorded leave events."""
        return list(self._events)

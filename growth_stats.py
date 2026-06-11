"""Pure growth/churn analytics for the /growth scoreboard.

Everything here is pure: it takes plain datetimes/dicts (pulled off live discord
objects and the join/leave logs by the caller) and returns plain numbers. No
discord, no env, no clock — `now` is always injected so the windows are
deterministic under test.
"""

from collections import Counter
from datetime import timedelta


def _parse(s):
    """Parse an ISO-8601 string (optionally 'Z'-suffixed) to a datetime, or None."""
    if not s:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def growth_windows(join_dates, leave_dates, now, windows=(7, 30, 90)):
    """Joins, leaves, and net change within each trailing window (in days).

    `join_dates`/`leave_dates` are lists of timezone-aware datetimes. Returns
    ``{days: {"joins": j, "leaves": l, "net": j - l}}``.
    """
    out = {}
    for days in windows:
        cutoff = now - timedelta(days=days)
        joins = sum(1 for d in join_dates if d and d >= cutoff)
        leaves = sum(1 for d in leave_dates if d and d >= cutoff)
        out[days] = {"joins": joins, "leaves": leaves, "net": joins - leaves}
    return out


def join_cohorts(join_dates, months=6):
    """Joins per calendar month (YYYY-MM), most recent `months` entries.

    Returns a list of ``(month_str, count)`` in chronological order.
    """
    counts = Counter()
    for d in join_dates:
        if d:
            counts[d.strftime("%Y-%m")] += 1
    ordered = sorted(counts.items())
    return ordered[-months:]


def top_inviters(join_records, current_user_ids, limit=10):
    """Rank inviters by how many of *their* recruits are still in the server.

    `join_records` come from the JoinLog (each may have inviter_tag + the joined
    user). `current_user_ids` is the set of users still present, so we only count
    members who actually stayed. Returns ``[(inviter_tag, kept_count)]``.
    """
    current = {str(u) for u in current_user_ids}
    counts = Counter()
    for rec in join_records:
        inviter = rec.get("inviter_tag")
        joined_id = rec.get("user_id")
        if not inviter:
            continue
        # If we know who joined, only count them when they're still here.
        if joined_id is not None and str(joined_id) not in current:
            continue
        counts[inviter] += 1
    return counts.most_common(limit)


def recent_leavers(leave_events, now, days=30, limit=10):
    """Most recent departures within `days`, newest first.

    Each event may carry joined_at; when present we compute tenure in days.
    Returns ``[{"username", "left_at", "tenure_days"}]``.
    """
    cutoff = now - timedelta(days=days)
    rows = []
    for ev in leave_events:
        left = _parse(ev.get("left_at"))
        if not left or left < cutoff:
            continue
        joined = _parse(ev.get("joined_at"))
        tenure = (left - joined).days if joined else None
        rows.append({
            "username": ev.get("username", "unknown"),
            "left_at": left,
            "tenure_days": tenure,
        })
    rows.sort(key=lambda r: r["left_at"], reverse=True)
    return rows[:limit]

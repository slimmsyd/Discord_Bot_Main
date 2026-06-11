import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from growth_stats import growth_windows, join_cohorts, top_inviters, recent_leavers

NOW = datetime(2026, 6, 11, tzinfo=timezone.utc)


def days_ago(n):
    return NOW - timedelta(days=n)


def test_growth_windows_counts_and_net():
    joins = [days_ago(2), days_ago(10), days_ago(40)]
    leaves = [days_ago(3), days_ago(80)]
    w = growth_windows(joins, leaves, NOW)
    assert w[7] == {"joins": 1, "leaves": 1, "net": 0}
    assert w[30] == {"joins": 2, "leaves": 1, "net": 1}
    assert w[90] == {"joins": 3, "leaves": 2, "net": 1}


def test_growth_windows_ignores_none_dates():
    w = growth_windows([days_ago(1), None], [None], NOW)
    assert w[7]["joins"] == 1
    assert w[7]["leaves"] == 0


def test_join_cohorts_groups_by_month_and_trims():
    joins = [
        datetime(2026, 4, 1, tzinfo=timezone.utc),
        datetime(2026, 5, 2, tzinfo=timezone.utc),
        datetime(2026, 5, 20, tzinfo=timezone.utc),
        datetime(2026, 6, 3, tzinfo=timezone.utc),
    ]
    assert join_cohorts(joins, months=6) == [("2026-04", 1), ("2026-05", 2), ("2026-06", 1)]
    assert join_cohorts(joins, months=2) == [("2026-05", 2), ("2026-06", 1)]


def test_top_inviters_only_counts_recruits_still_present():
    records = [
        {"user_id": "1", "inviter_tag": "alice"},
        {"user_id": "2", "inviter_tag": "alice"},   # this recruit left
        {"user_id": "3", "inviter_tag": "bob"},
        {"user_id": "4"},                            # no inviter -> ignored
    ]
    current = {"1", "3"}  # user 2 has left
    assert top_inviters(records, current) == [("alice", 1), ("bob", 1)]


def test_top_inviters_counts_when_user_id_unknown():
    # Older records without user_id can't be presence-checked; count them anyway.
    records = [{"inviter_tag": "carol"}, {"inviter_tag": "carol"}]
    assert top_inviters(records, set()) == [("carol", 2)]


def test_recent_leavers_window_order_and_tenure():
    events = [
        {"username": "old", "left_at": days_ago(40).isoformat()},        # outside 30d
        {"username": "x", "left_at": days_ago(5).isoformat(),
         "joined_at": days_ago(35).isoformat()},
        {"username": "y", "left_at": days_ago(1).isoformat()},           # no joined_at
    ]
    out = recent_leavers(events, NOW, days=30)
    assert [r["username"] for r in out] == ["y", "x"]   # newest first
    assert out[1]["tenure_days"] == 30                  # 35d - 5d
    assert out[0]["tenure_days"] is None                # missing joined_at

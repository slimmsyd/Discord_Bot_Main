import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conversation_memory import ConversationMemory, make_key


def test_make_key_distinguishes_channel_and_user():
    assert make_key(1, 2) != make_key(1, 3)
    assert make_key(1, 2) != make_key(9, 2)
    assert make_key(1, 2) == make_key(1, 2)


def test_get_history_empty_for_unknown_key():
    mem = ConversationMemory()
    assert mem.get_history(make_key(1, 2), now=1000.0) == []


def test_append_then_get_returns_turns_in_order():
    mem = ConversationMemory()
    k = make_key(1, 2)
    mem.append_turn(k, "user", "q1", now=1000.0)
    mem.append_turn(k, "assistant", "a1", now=1000.0)
    assert mem.get_history(k, now=1000.0) == [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
    ]


def test_max_turns_eviction_keeps_most_recent():
    mem = ConversationMemory(max_turns=2)
    k = make_key(1, 2)
    for i in range(4):
        mem.append_turn(k, "user", f"m{i}", now=1000.0)
    hist = mem.get_history(k, now=1000.0)
    assert [t["content"] for t in hist] == ["m2", "m3"]


def test_ttl_expiry_on_read():
    mem = ConversationMemory(ttl_seconds=1800)
    k = make_key(1, 2)
    mem.append_turn(k, "user", "q1", now=1000.0)
    assert mem.get_history(k, now=1000.0 + 1801) == []


def test_ttl_not_expired_within_window():
    mem = ConversationMemory(ttl_seconds=1800)
    k = make_key(1, 2)
    mem.append_turn(k, "user", "q1", now=1000.0)
    assert len(mem.get_history(k, now=1000.0 + 1700)) == 1


def test_append_refreshes_ttl():
    mem = ConversationMemory(ttl_seconds=1800)
    k = make_key(1, 2)
    mem.append_turn(k, "user", "q1", now=1000.0)
    mem.append_turn(k, "user", "q2", now=1000.0 + 1700)  # refreshes last-touched
    # 1600s after the LAST touch -> still alive
    assert len(mem.get_history(k, now=1000.0 + 3300)) == 2


def test_prune_evicts_only_stale_keys():
    mem = ConversationMemory(ttl_seconds=1800)
    fresh, stale = make_key(1, 1), make_key(2, 2)
    mem.append_turn(stale, "user", "old", now=1000.0)
    mem.append_turn(fresh, "user", "new", now=1000.0 + 1000)
    mem.prune(now=1000.0 + 1900)  # stale is 1900s old, fresh is 900s old
    assert mem.get_history(stale, now=1000.0 + 1900) == []
    assert len(mem.get_history(fresh, now=1000.0 + 1900)) == 1

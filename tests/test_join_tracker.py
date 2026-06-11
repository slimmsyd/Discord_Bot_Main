import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from join_tracker import diff_invite_uses, JoinLog


def test_detects_the_incremented_invite():
    before = {"abc": 3, "xyz": 7}
    after = {"abc": 4, "xyz": 7}
    assert diff_invite_uses(before, after) == "abc"


def test_brand_new_invite_used_on_join():
    before = {"abc": 3}
    after = {"abc": 3, "new1": 1}  # invite created and used in one step
    assert diff_invite_uses(before, after) == "new1"


def test_no_change_returns_none():
    snapshot = {"abc": 3, "xyz": 7}
    assert diff_invite_uses(snapshot, dict(snapshot)) is None


def test_vanity_or_consumed_invite_is_indeterminate():
    # Single-use invite consumed and auto-deleted: it's gone from `after`,
    # so there's no positive diff to attribute.
    before = {"once": 0}
    after = {}
    assert diff_invite_uses(before, after) is None


def test_joinlog_round_trips_through_disk(tmp_path):
    path = tmp_path / "join_log.json"
    log = JoinLog(str(path))
    log.record(123, {"method": "invite", "invite_code": "abc"})

    reloaded = JoinLog(str(path))
    assert reloaded.get(123) == {"method": "invite", "invite_code": "abc"}
    assert reloaded.get(999) is None


def test_joinlog_survives_corrupt_file(tmp_path):
    path = tmp_path / "join_log.json"
    path.write_text("{ not valid json")
    log = JoinLog(str(path))  # should not raise
    assert log.get(1) is None
    log.record(1, {"method": "unknown"})  # and is still usable
    assert JoinLog(str(path)).get(1) == {"method": "unknown"}

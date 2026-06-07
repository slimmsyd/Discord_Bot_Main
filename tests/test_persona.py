import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from persona import STREET_ORACLE_SYSTEM, build_messages


def test_build_messages_empty_history():
    msgs = build_messages("SYS", [], "hello")
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert msgs[0]["content"] == "SYS"
    assert msgs[-1] == {"role": "user", "content": "hello"}


def test_build_messages_includes_history_in_order():
    history = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
    ]
    msgs = build_messages("SYS", history, "q2")
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert [m["content"] for m in msgs] == ["SYS", "q1", "a1", "q2"]


def test_build_messages_does_not_mutate_history():
    history = [{"role": "user", "content": "q1"}]
    msgs = build_messages("SYS", history, "q2")
    assert history == [{"role": "user", "content": "q1"}]  # unchanged
    assert msgs is not history


def test_build_messages_uses_given_system():
    msgs = build_messages(STREET_ORACLE_SYSTEM, [], "x")
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == STREET_ORACLE_SYSTEM


def test_street_oracle_system_has_voice_markers():
    assert "Young God," in STREET_ORACLE_SYSTEM
    assert "5-7 sentences" in STREET_ORACLE_SYSTEM

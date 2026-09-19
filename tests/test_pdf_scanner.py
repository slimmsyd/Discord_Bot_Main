import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pdf_scanner import resolve_scope


class FakePermissions:
    def __init__(self, view_channel):
        self.view_channel = view_channel


class FakeChannel:
    """Shared test double for every pdf_scanner task in this file."""

    def __init__(self, name, channel_id, can_view=True, messages=(), threads=(),
                 category=None, error=None):
        self.name = name
        self.id = channel_id
        self.can_view = can_view
        self._messages = list(messages)
        self._threads = list(threads)
        self.category = category
        self._error = error
        self.history_calls = 0

    @property
    def threads(self):
        return self._threads

    def permissions_for(self, user):
        return FakePermissions(self.can_view)

    def history(self, limit=None):
        self.history_calls += 1
        messages = self._messages
        error = self._error

        async def generator():
            if error is not None:
                raise error
            for message in messages:
                yield message

        return generator()


USER = SimpleNamespace(id=1)


def test_resolve_scope_explicit_channel_wins_over_category():
    wanted = FakeChannel("books", 1)
    other = FakeChannel("ignored", 2)
    category = SimpleNamespace(text_channels=[other], name="Cat")
    guild = SimpleNamespace(text_channels=[other])

    assert resolve_scope(guild, category=category, channel=wanted, invoker=USER) == [wanted]


def test_resolve_scope_filters_unviewable_channels():
    visible = FakeChannel("visible", 1, can_view=True)
    hidden = FakeChannel("hidden", 2, can_view=False)
    guild = SimpleNamespace(text_channels=[visible, hidden])

    assert resolve_scope(guild, invoker=USER) == [visible]


def test_resolve_scope_raises_for_unviewable_explicit_channel():
    hidden = FakeChannel("secret", 2, can_view=False)
    guild = SimpleNamespace(text_channels=[hidden])

    try:
        resolve_scope(guild, channel=hidden, invoker=USER)
    except PermissionError as exc:
        assert str(exc) == "You don't have access to #secret."
    else:
        raise AssertionError("expected PermissionError")


def test_resolve_scope_uses_category_when_given():
    a = FakeChannel("a", 1)
    b = FakeChannel("b", 2)
    category = SimpleNamespace(text_channels=[a], name="Cat")
    guild = SimpleNamespace(text_channels=[a, b])

    assert resolve_scope(guild, category=category, invoker=USER) == [a]


def test_resolve_scope_includes_active_threads():
    thread = FakeChannel("thread", 3)
    parent = FakeChannel("books", 1, threads=[thread])
    guild = SimpleNamespace(text_channels=[parent])

    assert resolve_scope(guild, invoker=USER) == [parent, thread]


def test_resolve_scope_skips_unviewable_threads():
    thread = FakeChannel("thread", 3, can_view=False)
    parent = FakeChannel("books", 1, threads=[thread])
    guild = SimpleNamespace(text_channels=[parent])

    assert resolve_scope(guild, invoker=USER) == [parent]

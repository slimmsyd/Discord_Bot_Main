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


import asyncio
from datetime import datetime, timezone

import discord

from pdf_scanner import ScanResult, scan_channels


class FakeResponse:
    status = 403
    reason = "Forbidden"


class FakeAuthor:
    def __init__(self, name, author_id):
        self.display_name = name
        self.id = author_id


class FakeAttachment:
    def __init__(self, filename, size=1024, attachment_id=1):
        self.filename = filename
        self.size = size
        self.content_type = None
        self.id = attachment_id
        self.url = "https://cdn.example/a.pdf"


class FakeMessage:
    def __init__(self, message_id, attachments):
        self.id = message_id
        self.attachments = attachments
        self.created_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        self.author = FakeAuthor("neo", 7)


def test_scan_channels_collects_pdfs_and_annotates_duplicates():
    channel = FakeChannel(
        "books",
        10,
        messages=[
            FakeMessage(1, [FakeAttachment("a.pdf", attachment_id=1)]),
            FakeMessage(2, [FakeAttachment("a.pdf", attachment_id=2)]),
            FakeMessage(3, [FakeAttachment("notes.docx", attachment_id=3)]),
        ],
    )
    result = asyncio.run(scan_channels([channel]))

    assert isinstance(result, ScanResult)
    assert [r["filename"] for r in result.records] == ["a.pdf", "a.pdf"]
    assert [r["duplicates"] for r in result.records] == [2, 2]
    assert result.skipped == []


def test_scan_channels_skips_forbidden_channel_and_keeps_going():
    forbidden = FakeChannel(
        "locked", 1, error=discord.Forbidden(FakeResponse(), "Missing Access")
    )
    ok = FakeChannel("books", 2, messages=[FakeMessage(1, [FakeAttachment("a.pdf")])])

    result = asyncio.run(scan_channels([forbidden, ok]))

    assert result.skipped == ["locked"]
    assert [r["channel"] for r in result.records] == ["books"]


def test_scan_channels_reports_progress_per_channel():
    channels = [FakeChannel("a", 1), FakeChannel("b", 2), FakeChannel("c", 3)]
    seen = []

    async def on_progress(done, total):
        seen.append((done, total))

    asyncio.run(scan_channels(channels, on_progress=on_progress))
    assert seen == [(1, 3), (2, 3), (3, 3)]


def test_scan_channels_returns_empty_result_for_no_channels():
    result = asyncio.run(scan_channels([]))
    assert result.records == []
    assert result.skipped == []

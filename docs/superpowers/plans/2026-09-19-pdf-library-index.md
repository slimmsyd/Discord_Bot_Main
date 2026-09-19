# PDF Library Index (`/pdfs`, `/pdfexport`, `/pdflink`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three Discord slash commands that index every PDF uploaded to a channel, a category, or the whole server — with no files downloaded and nothing persisted.

**Architecture:** Pure, Discord-free logic lives in `pdf_index.py` (detection, record building, rendering, CSV/JSON export). Async Discord I/O lives in `pdf_scanner.py` (scope resolution, history scanning, a 10-minute in-memory cache). Thin command glue goes in `app.py`. Discord messages remain the only source of truth, so a redeploy loses nothing.

**Tech Stack:** Python 3, `discord.py` 2.3+, stdlib `csv`/`io`/`json`/`time`/`dataclasses`. **No new runtime dependencies.**

## Global Constraints

- **No new runtime dependencies.** `requirements.txt` must not change. The bot never opens a PDF, so no PDF library is needed.
- **No persistence.** No new JSON store, no database, no env var. Railway's disk is ephemeral; the index is re-derived from Discord on every scan.
- **Metadata only.** Only attachment `filename`, `size`, `content_type`, `id`, `url`, plus message/author IDs and timestamps are read. Never the file bytes.
- **Everything the user sees is ephemeral** (`defer(ephemeral=True)`), matching `/exportmembers` and `/surveyresults`.
- **Permission filtering is mandatory.** Never return a link for a channel the invoker cannot view. Use `channel.permissions_for(invoker).view_channel`.
- **Slash command names are lowercase** — `pdfs`, `pdfexport`, `pdflink`.
- **Reuse the existing Discord style:** `@discord.app_commands.describe(...)` (already used in `app.py`), `defer → work → send`, `interaction.followup.send(..., ephemeral=True)` in `except` blocks, and `logger.error(..., exc_info=True)`.
- **Copy verbatim** (user-visible strings):
  - Whole-server warning: `⚠️ Scanning the whole server — this may take a while…`
  - Progress text: `⚠️ Scanning… ({done}/{total} channels)`
  - No results: `No PDFs found in {label}.`
  - No access: `You don't have access to #{channel.name}.`
- **Run tests with the project venv:** `.venv/bin/python -m pytest tests/ -v`. The venv has Python 3.13 + `discord.py` 2.7.1, which works. Do **not** use the system `python3` — it has a stale `discord.py` 2.4.0 that cannot import on 3.13 (`ModuleNotFoundError: audioop`). Install pytest once if missing: `.venv/bin/pip install pytest`. The suite currently has **65 passing tests**; this plan adds **43**, so a full run must report **108 passed** — fewer means a regression.
- **Tests are plain pytest functions**, run from the repo root. Every test file starts with the repo-root `sys.path` insert (existing repo convention).
- **No async test plugin.** Async code is tested with `asyncio.run(...)` inside a normal synchronous test function, so `pytest-asyncio` is not needed.
- **`chunk_records` defaults to `max_chars=1700`,** not 1800: the `📚 N PDFs — scope` header and the "skipped channels" footer are added on top, and Discord caps messages at 2000 characters.

**Verification note:** the complete contents of `pdf_index.py` and `pdf_scanner.py` below, plus every assertion in the Task 1–6 tests, were dry-run end-to-end against this repo's `.venv` (discord.py 2.7.1) before this plan was written — 45/45 checks pass. The code as written is known-good; the work is transcription plus the `app.py` glue.

---

### Task 1: `pdf_index.py` — detection, sizing, classification

**Files:**
- Create: `pdf_index.py`
- Test: `tests/test_pdf_index.py`

**Interfaces:**
- Consumes: nothing (first task)
- Produces:
  - `PDF_MIME: str` — the constant `"application/pdf"`
  - `is_pdf(attachment) -> bool`
  - `format_size(n_bytes) -> str`
  - `classify_type(category_name, channel_name) -> str`

- [ ] **Step 1: Write the failing test**

Create `tests/test_pdf_index.py`:

```python
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pdf_index import classify_type, format_size, is_pdf


def test_is_pdf_detects_standard_content_type():
    attachment = SimpleNamespace(content_type="application/pdf", filename="book")
    assert is_pdf(attachment) is True


def test_is_pdf_falls_back_to_extension_when_content_type_missing():
    # Discord frequently reports content_type as None.
    attachment = SimpleNamespace(content_type=None, filename="book.pdf")
    assert is_pdf(attachment) is True


def test_is_pdf_accepts_uppercase_extension():
    attachment = SimpleNamespace(content_type=None, filename="BOOK.PDF")
    assert is_pdf(attachment) is True


def test_is_pdf_rejects_docx():
    attachment = SimpleNamespace(
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename="notes.docx",
    )
    assert is_pdf(attachment) is False


def test_is_pdf_rejects_name_that_merely_contains_pdf():
    # "mypdfnotes" has no .pdf extension and no PDF content type.
    attachment = SimpleNamespace(content_type=None, filename="mypdfnotes")
    assert is_pdf(attachment) is False


def test_format_size_bytes():
    assert format_size(500) == "500 B"
    assert format_size(0) == "0 B"


def test_format_size_kilobytes():
    assert format_size(1024) == "1 KB"
    assert format_size(819200) == "800 KB"


def test_format_size_megabytes_and_gigabytes():
    assert format_size(1258291) == "1.2 MB"
    assert format_size(1024 ** 3) == "1.0 GB"


def test_classify_type_prefers_category():
    assert classify_type("Philosophy", "stoicism") == "Philosophy"


def test_classify_type_falls_back_to_channel_name():
    assert classify_type(None, "self-help") == "Self Help"
    assert classify_type("", "general_chat") == "General Chat"


def test_classify_type_handles_missing_channel():
    assert classify_type(None, None) == "Unsorted"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pdf_index.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pdf_index'`

- [ ] **Step 3: Write minimal implementation**

Create `pdf_index.py`:

```python
"""Pure helpers for indexing PDF attachments.

No Discord and no I/O live here: every function takes plain, duck-typed objects
(anything with `.attachments`, `.author`, `.created_at`, `.id`, `.name`) or plain
dicts, so it can be unit tested without a gateway connection.
"""

import csv
import io
import json

PDF_MIME = "application/pdf"

CSV_COLUMNS = [
    "category",
    "channel",
    "channel_id",
    "filename",
    "type",
    "size",
    "size_bytes",
    "uploader",
    "uploader_id",
    "uploaded_at",
    "message_id",
    "attachment_id",
    "url",
]


def is_pdf(attachment):
    """True if the attachment is a PDF.

    Discord often reports `content_type` as None, so a `.pdf` filename is the
    reliable second signal; either one is enough.
    """
    if getattr(attachment, "content_type", None) == PDF_MIME:
        return True
    return str(getattr(attachment, "filename", "")).lower().endswith(".pdf")


def format_size(n_bytes):
    """Human-readable file size: 500 B, 800 KB, 1.2 MB, 1.0 GB."""
    n = int(n_bytes or 0)
    if n < 1024:
        return f"{n} B"
    kb = n / 1024
    if kb < 1024:
        return f"{kb:.0f} KB"
    mb = kb / 1024
    if mb < 1024:
        return f"{mb:.1f} MB"
    return f"{mb / 1024:.1f} GB"


def classify_type(category_name, channel_name):
    """The "what kind of document is this" label.

    The Discord category is the strongest signal; when a channel is not in a
    category we fall back to its own name, tidied up.
    """
    if category_name:
        return str(category_name)
    tidied = str(channel_name or "unsorted").replace("-", " ").replace("_", " ")
    return tidied.title() if tidied.strip() else "Unsorted"
```

`CSV_COLUMNS` is defined now but only used by Task 3; that is intentional, so the CSV header order lives in one place from the start.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_pdf_index.py -v`
Expected: PASS — 11 passed

- [ ] **Step 5: Commit**

```bash
git add pdf_index.py tests/test_pdf_index.py
git commit -m "feat: add PDF detection, size formatting, and type classification"
```

---

### Task 2: `pdf_index.py` — records, duplicate counts, rendering, chunking

**Files:**
- Modify: `pdf_index.py` (append)
- Test: `tests/test_pdf_index.py` (append)

**Interfaces:**
- Consumes: `is_pdf`, `format_size`, `classify_type`, `PDF_MIME`, `CSV_COLUMNS` (Task 1)
- Produces:
  - `collect_pdf_records(messages, channel, category_name=None) -> list[dict]` — record keys: `category`, `channel`, `channel_id`, `filename`, `type`, `size_bytes`, `size`, `uploader`, `uploader_id`, `uploaded_at`, `message_id`, `attachment_id`, `url`
  - `annotate_duplicates(records) -> list[dict]` — adds `duplicates: int`
  - `render_page(records, *, show_links=False) -> str`
  - `chunk_records(records, max_chars=1700, *, show_links=False) -> list[list[dict]]`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pdf_index.py`:

```python
from pdf_index import (
    annotate_duplicates,
    chunk_records,
    collect_pdf_records,
    render_page,
)


class FakeCategory:
    def __init__(self, name):
        self.name = name


class FakeChannel:
    def __init__(self, name, channel_id=10, category=None):
        self.name = name
        self.id = channel_id
        self.category = category


class FakeAuthor:
    def __init__(self, display_name, author_id):
        self.display_name = display_name
        self.id = author_id


class FakeAttachment:
    def __init__(self, filename, size=1024, content_type=None, attachment_id=1,
                 url="https://cdn.example/a.pdf"):
        self.filename = filename
        self.size = size
        self.content_type = content_type
        self.id = attachment_id
        self.url = url


class FakeMessage:
    def __init__(self, message_id, attachments, created_at, author=None):
        self.id = message_id
        self.attachments = attachments
        self.created_at = created_at
        self.author = author or FakeAuthor("neo", 7)


UPLOADED = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def make_record(filename, channel="books", channel_id="10"):
    return {
        "category": "",
        "channel": channel,
        "channel_id": channel_id,
        "filename": filename,
        "type": "Books",
        "size_bytes": 1024,
        "size": "1 KB",
        "uploader": "neo",
        "uploader_id": "7",
        "uploaded_at": "2026-01-02T03:04:05+00:00",
        "message_id": "333",
        "attachment_id": "444",
        "url": "https://cdn.example/x",
        "duplicates": 1,
    }


def test_collect_maps_every_field():
    channel = FakeChannel("books", 10, category=FakeCategory("Philosophy"))
    attachment = FakeAttachment(
        "meditations.pdf", size=1258291, attachment_id=99, url="https://cdn/x"
    )
    message = FakeMessage(333, [attachment], UPLOADED, FakeAuthor("neo", 7))

    records = collect_pdf_records([message], channel)

    assert len(records) == 1
    record = records[0]
    assert record["filename"] == "meditations.pdf"
    assert record["type"] == "Philosophy"
    assert record["category"] == "Philosophy"
    assert record["channel"] == "books"
    assert record["channel_id"] == "10"
    assert record["size"] == "1.2 MB"
    assert record["size_bytes"] == 1258291
    assert record["uploader"] == "neo"
    assert record["uploader_id"] == "7"
    assert record["uploaded_at"] == "2026-01-02T03:04:05+00:00"
    assert record["message_id"] == "333"
    assert record["attachment_id"] == "99"
    assert record["url"] == "https://cdn/x"


def test_collect_skips_non_pdf_attachments():
    channel = FakeChannel("books")
    message = FakeMessage(
        1,
        [FakeAttachment("notes.docx"), FakeAttachment("real.pdf")],
        UPLOADED,
    )
    records = collect_pdf_records([message], channel)
    assert [r["filename"] for r in records] == ["real.pdf"]


def test_collect_handles_message_with_no_attachments():
    channel = FakeChannel("books")
    assert collect_pdf_records([FakeMessage(1, [], UPLOADED)], channel) == []


def test_collect_takes_multiple_pdfs_from_one_message():
    channel = FakeChannel("books")
    message = FakeMessage(1, [FakeAttachment("a.pdf"), FakeAttachment("b.pdf")], UPLOADED)
    records = collect_pdf_records([message], channel)
    assert [r["filename"] for r in records] == ["a.pdf", "b.pdf"]


def test_collect_explicit_category_overrides_channel_category():
    channel = FakeChannel("books", 10, category=FakeCategory("Wrong"))
    message = FakeMessage(1, [FakeAttachment("a.pdf")], UPLOADED)
    records = collect_pdf_records([message], channel, category_name="Philosophy")
    assert records[0]["type"] == "Philosophy"


def test_collect_leaves_uploaded_at_blank_without_timestamp():
    channel = FakeChannel("books")
    message = FakeMessage(1, [FakeAttachment("a.pdf")], None)
    assert collect_pdf_records([message], channel)[0]["uploaded_at"] == ""


def test_annotate_duplicates_counts_same_filename_per_channel():
    records = [
        make_record("a.pdf"),
        make_record("a.pdf"),
        make_record("b.pdf"),
        make_record("a.pdf", channel="other", channel_id="11"),
    ]
    annotate_duplicates(records)
    assert [r["duplicates"] for r in records] == [2, 2, 1, 1]


def test_render_page_groups_by_channel_and_truncates_date():
    records = [
        make_record("a.pdf", channel="books"),
        make_record("b.pdf", channel="stoicism", channel_id="11"),
    ]
    text = render_page(records)
    assert "── #books · Books ──" in text
    assert "── #stoicism · Books ──" in text
    assert "• a.pdf — 1 KB — neo — 2026-01-02" in text


def test_render_page_appends_links_only_when_asked():
    records = [make_record("a.pdf")]
    assert "download" not in render_page(records)
    assert "[download](https://cdn.example/x)" in render_page(records, show_links=True)


def test_render_page_marks_duplicate_filenames():
    records = [make_record("a.pdf"), make_record("a.pdf")]
    annotate_duplicates(records)
    assert "(×2)" in render_page(records)


def test_render_page_empty():
    assert render_page([]) == ""


def test_chunk_records_keeps_one_page_when_everything_fits():
    records = [make_record(f"book-{i}.pdf") for i in range(6)]
    assert chunk_records(records, max_chars=100000) == [records]


def test_chunk_records_splits_and_loses_nothing():
    records = [make_record(f"book-{i}.pdf") for i in range(6)]
    pages = chunk_records(records, max_chars=120)
    assert len(pages) > 1
    assert [r for page in pages for r in page] == records
    for page in pages:
        if len(page) > 1:
            assert len(render_page(page)) <= 120


def test_chunk_records_returns_one_empty_page_for_no_records():
    assert chunk_records([], max_chars=100) == [[]]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pdf_index.py -v`
Expected: FAIL — `ImportError: cannot import name 'annotate_duplicates' from 'pdf_index'`

- [ ] **Step 3: Write minimal implementation**

Append to `pdf_index.py`:

```python
def _attachment_category_name(channel):
    """The channel's Discord category name, or None when it has no category."""
    category = getattr(channel, "category", None)
    return getattr(category, "name", None)


def collect_pdf_records(messages, channel, category_name=None):
    """Build one record per PDF attachment found in `messages`.

    `messages` is any iterable of message-like objects. The scanner calls this
    one message at a time, so memory stays flat on long channels.
    """
    resolved_category = (
        category_name if category_name is not None else _attachment_category_name(channel)
    )
    channel_name = getattr(channel, "name", "") or ""
    channel_id = str(getattr(channel, "id", "") or "")
    record_type = classify_type(resolved_category, channel_name)

    records = []
    for message in messages:
        author = getattr(message, "author", None)
        uploader = (
            getattr(author, "display_name", None)
            or getattr(author, "name", None)
            or "unknown"
        )
        created_at = getattr(message, "created_at", None)
        uploaded_at = created_at.isoformat() if created_at is not None else ""

        for attachment in getattr(message, "attachments", None) or []:
            if not is_pdf(attachment):
                continue
            size_bytes = int(getattr(attachment, "size", 0) or 0)
            records.append(
                {
                    "category": resolved_category or "",
                    "channel": channel_name,
                    "channel_id": channel_id,
                    "filename": getattr(attachment, "filename", "") or "unknown.pdf",
                    "type": record_type,
                    "size_bytes": size_bytes,
                    "size": format_size(size_bytes),
                    "uploader": uploader,
                    "uploader_id": str(getattr(author, "id", "") or ""),
                    "uploaded_at": uploaded_at,
                    "message_id": str(getattr(message, "id", "") or ""),
                    "attachment_id": str(getattr(attachment, "id", "") or ""),
                    "url": getattr(attachment, "url", "") or "",
                }
            )
    return records


def annotate_duplicates(records):
    """Set `duplicates` = how many times that filename appears in the same channel."""
    counts = {}
    for record in records:
        key = (record.get("channel_id", ""), record.get("filename", ""))
        counts[key] = counts.get(key, 0) + 1
    for record in records:
        key = (record.get("channel_id", ""), record.get("filename", ""))
        record["duplicates"] = counts[key]
    return records


def render_page(records, *, show_links=False):
    """Render a page of records as Discord message text, grouped by channel."""
    if not records:
        return ""
    lines = []
    last_channel = None
    for record in records:
        if record.get("channel") != last_channel:
            lines.append(f"── #{record.get('channel', '?')} · {record.get('type', '')} ──")
            last_channel = record.get("channel")
        uploaded = str(record.get("uploaded_at", ""))[:10]
        line = (
            f"• {record.get('filename', '?')} — {record.get('size', '?')} — "
            f"{record.get('uploader', '?')} — {uploaded}"
        )
        if int(record.get("duplicates", 1) or 1) > 1:
            line += f" (×{record['duplicates']})"
        if show_links and record.get("url"):
            line += f" — [download]({record['url']})"
        lines.append(line)
    return "\n".join(lines)


def chunk_records(records, max_chars=1700, *, show_links=False):
    """Split records into pages whose rendered text stays under `max_chars`.

    1700 leaves headroom for the caller's header and footer under Discord's
    2000-character message cap. Always returns at least one page (possibly
    empty) so callers never special-case an empty index.
    """
    pages = []
    current = []
    for record in records:
        candidate = current + [record]
        if current and len(render_page(candidate, show_links=show_links)) > max_chars:
            pages.append(current)
            current = [record]
        else:
            current = candidate
    if current or not pages:
        pages.append(current)
    return pages
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_pdf_index.py -v`
Expected: PASS — 25 passed

- [ ] **Step 5: Commit**

```bash
git add pdf_index.py tests/test_pdf_index.py
git commit -m "feat: build PDF records, page rendering, and chunking"
```

---

### Task 3: `pdf_index.py` — CSV and JSON exporters

**Files:**
- Modify: `pdf_index.py` (append two functions)
- Test: `tests/test_pdf_index.py` (append)

**Interfaces:**
- Consumes: `CSV_COLUMNS` (Task 1), record dicts (Task 2)
- Produces: `build_pdf_csv(records) -> str`, `build_pdf_json(records) -> str`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pdf_index.py`:

```python
import csv as csv_module
import io as io_module
import json

from pdf_index import CSV_COLUMNS, build_pdf_csv, build_pdf_json


def test_csv_header_is_the_full_column_list():
    rows = list(csv_module.reader(io_module.StringIO(build_pdf_csv([]))))
    assert rows[0] == CSV_COLUMNS


def test_csv_round_trips_a_row():
    record = make_record("a.pdf")
    rows = list(csv_module.reader(io_module.StringIO(build_pdf_csv([record]))))
    assert len(rows) == 2
    row = dict(zip(rows[0], rows[1]))
    assert row["filename"] == "a.pdf"
    assert row["message_id"] == "333"
    assert row["attachment_id"] == "444"
    assert row["url"] == "https://cdn.example/x"


def test_csv_escapes_commas_and_quotes_in_filenames():
    record = make_record('weird, "quoted" name.pdf')
    rows = list(csv_module.reader(io_module.StringIO(build_pdf_csv([record]))))
    assert rows[1][CSV_COLUMNS.index("filename")] == 'weird, "quoted" name.pdf'


def test_json_round_trips_the_records():
    records = [make_record("a.pdf"), make_record("b.pdf")]
    assert json.loads(build_pdf_json(records)) == records
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pdf_index.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_pdf_csv' from 'pdf_index'` (the 4 new tests error on import)

- [ ] **Step 3: Write minimal implementation**

Append to `pdf_index.py`:

```python
def build_pdf_csv(records):
    """CSV text, one row per PDF, with a stable column order (CSV_COLUMNS)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for record in records:
        writer.writerow([record.get(column, "") for column in CSV_COLUMNS])
    return buffer.getvalue()


def build_pdf_json(records):
    """Pretty-printed JSON array of the same records, stable IDs included."""
    return json.dumps(records, indent=2)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_pdf_index.py -v`
Expected: PASS — 29 passed

- [ ] **Step 5: Commit**

```bash
git add pdf_index.py tests/test_pdf_index.py
git commit -m "feat: add CSV and JSON PDF index exporters"
```

---

### Task 4: `pdf_scanner.py` — scope resolution and permissions

**Files:**
- Create: `pdf_scanner.py`
- Test: `tests/test_pdf_scanner.py`

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces:
  - `_can_view(channel, invoker) -> bool` (internal)
  - `resolve_scope(guild, *, category=None, channel=None, invoker=None) -> list` — raises `PermissionError` when an explicitly named channel is not viewable

- [ ] **Step 1: Write the failing test**

Create `tests/test_pdf_scanner.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pdf_scanner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pdf_scanner'`

- [ ] **Step 3: Write minimal implementation**

Create `pdf_scanner.py`:

```python
"""Async Discord I/O for the PDF index: scope resolution, scanning, caching.

Discord messages are the only source of truth. Nothing here writes to disk, so
a redeploy loses the cache and nothing else.
"""

import logging

logger = logging.getLogger("discord_bot")


def _can_view(channel, invoker):
    """True if `invoker` may view `channel`. Fails closed."""
    if invoker is None:
        return True
    try:
        return bool(channel.permissions_for(invoker).view_channel)
    except Exception:
        logger.warning("Could not check permissions for #%s", getattr(channel, "name", "?"))
        return False


def _threads_of(channel, invoker):
    """Active threads of a text channel, filtered to what `invoker` can view."""
    return [t for t in (getattr(channel, "threads", None) or []) if _can_view(t, invoker)]


def resolve_scope(guild, *, category=None, channel=None, invoker=None):
    """The channels to scan.

    An explicit `channel` wins over `category`; neither means the whole guild.
    Results are always filtered to channels `invoker` can actually view, so the
    index never leaks a link the caller could not open in Discord.
    """
    if channel is not None:
        if not _can_view(channel, invoker):
            raise PermissionError(f"You don't have access to #{channel.name}.")
        return [channel] + _threads_of(channel, invoker)

    if category is not None:
        candidates = list(category.text_channels)
    else:
        candidates = list(guild.text_channels)

    expanded = []
    for candidate in candidates:
        if not _can_view(candidate, invoker):
            continue
        expanded.append(candidate)
        expanded.extend(_threads_of(candidate, invoker))
    return expanded
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_pdf_scanner.py -v`
Expected: PASS — 6 passed

- [ ] **Step 5: Commit**

```bash
git add pdf_scanner.py tests/test_pdf_scanner.py
git commit -m "feat: resolve PDF scan scope with view-permission filtering"
```

---

### Task 5: `pdf_scanner.py` — `scan_channels` with per-channel error isolation

**Files:**
- Modify: `pdf_scanner.py` (extend the import block; append `ScanResult` and `scan_channels`)
- Test: `tests/test_pdf_scanner.py` (append)

**Interfaces:**
- Consumes: `collect_pdf_records`, `annotate_duplicates` (Task 2)
- Produces: `ScanResult` dataclass (`records: list`, `skipped: list`), `scan_channels(channels, *, on_progress=None) -> ScanResult`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pdf_scanner.py`:

```python
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
```

`discord.Forbidden(FakeResponse(), "Missing Access")` is the real exception type, so the scanner's `except discord.Forbidden` is genuinely exercised. `FakeResponse` needs both `.status` and `.reason`, which `HTTPException.__init__` reads when formatting its message.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pdf_scanner.py -v`
Expected: FAIL — `ImportError: cannot import name 'ScanResult' from 'pdf_scanner'`

- [ ] **Step 3: Write minimal implementation**

Replace the top import block of `pdf_scanner.py`:

```python
import logging
import time
from dataclasses import dataclass, field

import discord

from pdf_index import annotate_duplicates, collect_pdf_records

logger = logging.getLogger("discord_bot")
```

(`import time` is used by Task 6; adding it here keeps the import block whole.)

Then append to `pdf_scanner.py`:

```python
@dataclass
class ScanResult:
    """Everything one scan produced: the PDF records, plus channels we could not read."""

    records: list = field(default_factory=list)
    skipped: list = field(default_factory=list)


async def scan_channels(channels, *, on_progress=None):
    """Scan `channels` for PDF attachments, never letting one channel abort the run.

    `on_progress(done, total)` is awaited after each channel so the command can
    edit its "scanning…" message.
    """
    result = ScanResult()
    total = len(channels)

    for index, channel in enumerate(channels, start=1):
        name = getattr(channel, "name", "?")
        try:
            async for message in channel.history(limit=None):
                result.records.extend(collect_pdf_records([message], channel))
        except discord.Forbidden:
            logger.warning("Skipping #%s: missing Read Message History permission", name)
            result.skipped.append(name)
        except discord.HTTPException as exc:
            logger.error("Discord error while scanning #%s: %s", name, exc)
            result.skipped.append(name)

        if on_progress is not None:
            await on_progress(index, total)

    annotate_duplicates(result.records)
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_pdf_scanner.py -v`
Expected: PASS — 10 passed

- [ ] **Step 5: Commit**

```bash
git add pdf_scanner.py tests/test_pdf_scanner.py
git commit -m "feat: scan channels for PDFs with per-channel error isolation"
```

---

### Task 6: `pdf_scanner.py` — TTL cache

**Files:**
- Modify: `pdf_scanner.py` (append)
- Test: `tests/test_pdf_scanner.py` (append)

**Interfaces:**
- Consumes: `scan_channels` (Task 5)
- Produces: `CACHE_TTL: int`, `clear_cache() -> None`, `scan_with_cache(channels, *, on_progress=None, ttl=CACHE_TTL, clock=time.monotonic) -> ScanResult`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pdf_scanner.py`:

```python
from pdf_scanner import clear_cache, scan_with_cache


def test_scan_with_cache_hits_within_ttl():
    clear_cache()
    channel = FakeChannel("books", 1, messages=[FakeMessage(1, [FakeAttachment("a.pdf")])])
    clock = lambda: 100.0

    first = asyncio.run(scan_with_cache([channel], clock=clock))
    second = asyncio.run(scan_with_cache([channel], clock=clock))

    assert channel.history_calls == 1
    assert first.records == second.records


def test_scan_with_cache_misses_after_ttl():
    clear_cache()
    channel = FakeChannel("books", 1, messages=[FakeMessage(1, [FakeAttachment("a.pdf")])])
    now = {"t": 100.0}

    asyncio.run(scan_with_cache([channel], clock=lambda: now["t"]))
    now["t"] += 700
    asyncio.run(scan_with_cache([channel], clock=lambda: now["t"]))

    assert channel.history_calls == 2


def test_scan_with_cache_keys_on_the_channel_set():
    clear_cache()
    a = FakeChannel("a", 1)
    b = FakeChannel("b", 2)

    asyncio.run(scan_with_cache([a], clock=lambda: 100.0))
    asyncio.run(scan_with_cache([a, b], clock=lambda: 100.0))

    assert a.history_calls == 2


def test_clear_cache_forces_a_rescan():
    clear_cache()
    channel = FakeChannel("books", 1)
    asyncio.run(scan_with_cache([channel], clock=lambda: 100.0))
    clear_cache()
    asyncio.run(scan_with_cache([channel], clock=lambda: 100.0))
    assert channel.history_calls == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pdf_scanner.py -v`
Expected: FAIL — `ImportError: cannot import name 'clear_cache' from 'pdf_scanner'`

- [ ] **Step 3: Write minimal implementation**

Append to `pdf_scanner.py`:

```python
CACHE_TTL = 600

_CACHE = {}


def clear_cache():
    """Drop every cached scan. Used by tests; harmless in production."""
    _CACHE.clear()


def _cache_key(channels):
    return tuple(sorted(str(channel.id) for channel in channels))


async def scan_with_cache(channels, *, on_progress=None, ttl=CACHE_TTL, clock=time.monotonic):
    """`scan_channels`, memoised per channel-set for `ttl` seconds.

    The cache lives in memory only. Discord signed URLs stay valid for ~24h, so
    a 10-minute cache can never hand out an expired link.
    """
    key = _cache_key(channels)
    now = clock()
    cached = _CACHE.get(key)
    if cached is not None and now - cached[0] < ttl:
        return cached[1]

    result = await scan_channels(channels, on_progress=on_progress)
    _CACHE[key] = (now, result)
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_pdf_scanner.py -v`
Expected: PASS — 14 passed

Then run the whole suite: `.venv/bin/python -m pytest tests/ -v` — Expected: 108 passed (65 pre-existing + 43 new).

- [ ] **Step 5: Commit**

```bash
git add pdf_scanner.py tests/test_pdf_scanner.py
git commit -m "feat: memoise PDF scans with a 10-minute TTL cache"
```

---

### Task 7: `app.py` — `/pdfs` command with paging and category autocomplete

**Files:**
- Modify: `app.py:28` (add imports next to `from survey_store import ...`)
- Modify: `app.py:1388` (insert the PDF block after `closesurvey`, before `@bot.event` / `async def on_command_error`)
- Test: manual in the Discord test server (this task is command glue)

**Interfaces:**
- Consumes: `render_page`, `chunk_records` (Task 2); `resolve_scope` (Task 4); `scan_with_cache` (Task 6)
- Produces: `PdfPagesView`, `_scope_label`, `_scope_token`, `_category_autocomplete`, `_resolve_scope_or_reply`, `_progress_callback`, and the `pdfs` command — Tasks 8 and 9 reuse all of these

- [ ] **Step 1: Add the imports**

Immediately after the line `from survey_store import SurveyStore, build_survey_csv, survey_message_text` add:

```python
from pdf_index import build_pdf_csv, build_pdf_json, chunk_records, render_page
from pdf_scanner import resolve_scope, scan_with_cache
```

Note `build_pdf_csv`/`build_pdf_json` are unused until Task 8; importing them now keeps this block in one place. If an unused-import lint blocks the commit, add them in Task 8 instead.

- [ ] **Step 2: Insert the shared block and the `/pdfs` command**

Insert immediately after `closesurvey`'s final line (currently `await interaction.followup.send(f"Close glitched out. Error: {str(e)}", ephemeral=True)`) and before `@bot.event`:

```python
# ---------------------------------------------------------------------------
# PDF library index — /pdfs, /pdfexport, /pdflink
# ---------------------------------------------------------------------------

class PdfPagesView(discord.ui.View):
    """Prev/Next paging for a long PDF index. Only the invoker can page it."""

    def __init__(self, author_id, pages, *, header, show_links, footer=""):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.pages = pages
        self.header = header
        self.show_links = show_links
        self.footer = footer
        self.index = 0
        self._sync_buttons()

    async def interaction_check(self, interaction: discord.Interaction):
        return interaction.user.id == self.author_id

    def _sync_buttons(self):
        self.previous.disabled = self.index == 0
        self.next.disabled = self.index >= len(self.pages) - 1
        self.counter.label = f"{self.index + 1}/{len(self.pages)}"

    def render(self):
        body = render_page(self.pages[self.index], show_links=self.show_links)
        return f"{self.header}\n{body}{self.footer}"

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index -= 1
        self._sync_buttons()
        await interaction.response.edit_message(content=self.render(), view=self)

    @discord.ui.button(label="1/1", style=discord.ButtonStyle.secondary, disabled=True)
    async def counter(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Label-only button showing the current page."""

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.primary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index += 1
        self._sync_buttons()
        await interaction.response.edit_message(content=self.render(), view=self)


def _scope_label(category_name, channel):
    """Human-readable description of what is being scanned."""
    if channel is not None:
        return f"#{channel.name}"
    if category_name:
        return category_name
    return "the whole server"


def _scope_token(category_name, channel):
    """Filename-safe token for the export files."""
    if channel is not None:
        return channel.name
    if category_name:
        return category_name.replace(" ", "-").lower()
    return "all"


async def _category_autocomplete(interaction, current):
    """Suggest the server's real category names as the user types."""
    if interaction.guild is None:
        return []
    needle = (current or "").lower()
    return [
        discord.app_commands.Choice(name=category.name, value=category.name)
        for category in interaction.guild.categories
        if needle in category.name.lower()
    ][:25]


async def _resolve_scope_or_reply(interaction, category_name, channel):
    """Resolve the channel list, replying with a friendly error when impossible.

    Returns None when a reply was already sent, so callers just return.
    """
    guild = interaction.guild
    if guild is None:
        await interaction.edit_original_response(content="This only works inside a server.")
        return None

    category = None
    if category_name:
        category = discord.utils.get(guild.categories, name=category_name)
        if category is None:
            await interaction.edit_original_response(
                content=f"No category named `{category_name}`."
            )
            return None

    try:
        channels = resolve_scope(
            guild, category=category, channel=channel, invoker=interaction.user
        )
    except PermissionError as exc:
        await interaction.edit_original_response(content=str(exc))
        return None

    if not channels:
        await interaction.edit_original_response(content="Nothing to scan in that scope.")
        return None
    return channels


def _progress_callback(interaction):
    """Throttled scan-progress edits, at most one every 2 seconds."""
    state = {"last": 0.0}

    async def on_progress(done, total):
        now = time.monotonic()
        if done < total and now - state["last"] < 2.0:
            return
        state["last"] = now
        try:
            await interaction.edit_original_response(
                content=f"⚠️ Scanning… ({done}/{total} channels)"
            )
        except discord.HTTPException:
            pass  # progress is best-effort and must never break the scan

    return on_progress


@bot.tree.command(
    name="pdfs", description="Index the PDFs in a channel, a category, or the whole server"
)
@discord.app_commands.describe(
    category="Scan every channel in this category",
    channel="Scan this one channel (takes precedence over category)",
    show_links="Include a clickable download link for each file",
)
@discord.app_commands.autocomplete(category=_category_autocomplete)
async def pdfs(
    interaction: discord.Interaction,
    category: str = None,
    channel: discord.TextChannel = None,
    show_links: bool = False,
):
    logger.info(f'pdfs requested by {interaction.user}: category={category} channel={channel}')

    try:
        await interaction.response.defer(ephemeral=True, thinking=True)

        if not category and channel is None:
            await interaction.edit_original_response(
                content="⚠️ Scanning the whole server — this may take a while…"
            )

        channels = await _resolve_scope_or_reply(interaction, category, channel)
        if channels is None:
            return

        result = await scan_with_cache(channels, on_progress=_progress_callback(interaction))
        label = _scope_label(category, channel)

        if not result.records:
            await interaction.edit_original_response(content=f"No PDFs found in {label}.")
            return

        header = f"📚 **{len(result.records)} PDFs** — {label}"
        footer = ""
        if result.skipped:
            names = ", ".join(f"#{name}" for name in result.skipped[:10])
            footer = f"\n\n_Skipped {len(result.skipped)} channel(s) I can't read: {names}_"

        pages = chunk_records(result.records, show_links=show_links)
        if len(pages) == 1:
            body = render_page(pages[0], show_links=show_links)
            await interaction.edit_original_response(content=f"{header}\n{body}{footer}")
            return

        view = PdfPagesView(
            interaction.user.id, pages, header=header, show_links=show_links, footer=footer
        )
        await interaction.edit_original_response(content=view.render(), view=view)

    except Exception as e:
        logger.error(f'Error in pdfs command: {str(e)}', exc_info=True)
        await interaction.followup.send(f"Index glitched out. Error: {str(e)}", ephemeral=True)
```

- [ ] **Step 3: Verify the module parses and the suite still passes**

Run: `.venv/bin/python -c "import ast, pathlib; ast.parse(pathlib.Path('app.py').read_text()); print('syntax ok')"`
Expected: `syntax ok`

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: 108 passed (nothing imports `app.py`, so this proves nothing else broke)

- [ ] **Step 4: Manual verification in Discord**

1. Restart the bot. Confirm the logs show `Synced` and `/pdfs` appears in the command list.
2. `/pdfs channel:#books` → ephemeral list grouped by channel with type labels.
3. `/pdfs category:Philosophy` → typing `Phil` in the category box shows autocomplete choices.
4. `/pdfs` with no options → the whole-server warning appears, then progress text.
5. `/pdfs channel:#books show_links:True` → each line gains a `[download]` link that opens the PDF.
6. A channel with no PDFs → `No PDFs found in #that-channel.`
7. Immediately re-run `/pdfs` for the same scope → returns fast (cache hit); the bot log shows no second scan.
8. Revoke the bot's **Read Message History** permission in one channel, then `/pdfs category:<that category>` → that channel is listed in the `_Skipped N channel(s) I can't read_` footer and the rest of the index still renders.

- [ ] **Step 5: Commit**

```bash
git add app.py
git commit -m "feat: add /pdfs index command with paging and category autocomplete"
```

---

### Task 8: `app.py` — `/pdfexport` command

**Files:**
- Modify: `app.py` (insert after the `pdfs` command from Task 7)
- Test: manual in the Discord test server

**Interfaces:**
- Consumes: `build_pdf_csv`, `build_pdf_json` (Task 3); `_scope_token`, `_scope_label`, `_category_autocomplete`, `_resolve_scope_or_reply`, `_progress_callback` (Task 7); `scan_with_cache` (Task 6)
- Produces: the `pdfexport` command

- [ ] **Step 1: Insert the command**

Insert immediately after the `pdfs` function body:

```python
@bot.tree.command(
    name="pdfexport", description="Export the PDF index for a channel or category as CSV + JSON"
)
@discord.app_commands.describe(
    category="Export every channel in this category",
    channel="Export this one channel (takes precedence over category)",
)
@discord.app_commands.autocomplete(category=_category_autocomplete)
async def pdfexport(
    interaction: discord.Interaction,
    category: str = None,
    channel: discord.TextChannel = None,
):
    logger.info(f'pdfexport requested by {interaction.user}: category={category} channel={channel}')

    try:
        await interaction.response.defer(ephemeral=True, thinking=True)

        if not category and channel is None:
            await interaction.edit_original_response(
                content="⚠️ Scanning the whole server — this may take a while…"
            )

        channels = await _resolve_scope_or_reply(interaction, category, channel)
        if channels is None:
            return

        result = await scan_with_cache(channels, on_progress=_progress_callback(interaction))
        label = _scope_label(category, channel)

        if not result.records:
            await interaction.edit_original_response(content=f"No PDFs found in {label}.")
            return

        token = _scope_token(category, channel)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        files = [
            discord.File(
                io.BytesIO(build_pdf_csv(result.records).encode("utf-8")),
                filename=f"pdf_index_{token}_{stamp}.csv",
            ),
            discord.File(
                io.BytesIO(build_pdf_json(result.records).encode("utf-8")),
                filename=f"pdf_index_{token}_{stamp}.json",
            ),
        ]

        note = f" Skipped {len(result.skipped)} channel(s) I can't read." if result.skipped else ""
        await interaction.edit_original_response(
            content=(
                f"📚 {len(result.records)} PDFs in {label}.{note}\n"
                "The CSV is for reading; the JSON keeps the stable IDs "
                "(`channel_id`, `message_id`, `attachment_id`) for re-linking later."
            ),
            attachments=files,
        )

    except Exception as e:
        logger.error(f'Error in pdfexport command: {str(e)}', exc_info=True)
        await interaction.followup.send(f"Export glitched out. Error: {str(e)}", ephemeral=True)
```

- [ ] **Step 2: Verify the module parses and the suite still passes**

Run: `.venv/bin/python -c "import ast, pathlib; ast.parse(pathlib.Path('app.py').read_text()); print('syntax ok')"`
Expected: `syntax ok`

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: 108 passed

- [ ] **Step 3: Manual verification in Discord**

1. `/pdfexport channel:#books` → ephemeral message with two files attached.
2. Download the CSV and confirm the columns are exactly: `category`, `channel`, `channel_id`, `filename`, `type`, `size`, `size_bytes`, `uploader`, `uploader_id`, `uploaded_at`, `message_id`, `attachment_id`, `url`.
3. Confirm the `url` value opens the PDF.
4. Open the JSON and confirm the same rows plus `duplicates`.
5. `/pdfexport` for a channel with no PDFs → `No PDFs found in …`, no files attached.

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "feat: add /pdfexport command producing CSV + JSON manifests"
```

---

### Task 9: `app.py` — `/pdflink` command

**Files:**
- Modify: `app.py` (insert after the `pdfexport` command from Task 8)
- Test: manual in the Discord test server

**Interfaces:**
- Consumes: `resolve_scope` (Task 4); `scan_with_cache` (Task 6)
- Produces: the `pdflink` command and `_fresh_url(guild, record) -> str | None`

- [ ] **Step 1: Insert the command and the link refresher**

Insert immediately after the `pdfexport` function body:

```python
async def _fresh_url(guild, record):
    """Re-fetch the source message so Discord signs a brand-new attachment URL.

    Discord CDN links expire in roughly 24 hours, so a stored URL is useless
    later. Re-reading the message is what makes "download it later" work.
    Returns None when the message no longer exists.
    """
    target = guild.get_channel_or_thread(int(record["channel_id"]))
    if target is None:
        return None

    try:
        message = await target.fetch_message(int(record["message_id"]))
    except discord.NotFound:
        return None
    except (discord.Forbidden, discord.HTTPException):
        return record.get("url") or None

    for attachment in message.attachments:
        if str(attachment.id) == record.get("attachment_id"):
            return attachment.url
    return record.get("url") or None


@bot.tree.command(
    name="pdflink", description="Get a fresh download link for a PDF by filename"
)
@discord.app_commands.describe(
    channel="Channel to search",
    name="Part of the filename to look for",
)
async def pdflink(interaction: discord.Interaction, channel: discord.TextChannel, name: str):
    logger.info(f'pdflink requested by {interaction.user}: #{channel.name} name={name}')

    try:
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            channels = resolve_scope(
                interaction.guild, channel=channel, invoker=interaction.user
            )
        except PermissionError as exc:
            await interaction.edit_original_response(content=str(exc))
            return

        result = await scan_with_cache(channels)

        fragment = name.strip().lower()
        matches = [record for record in result.records if fragment in record["filename"].lower()]

        if not matches:
            await interaction.edit_original_response(
                content=f"No PDF matching `{name}` in #{channel.name}."
            )
            return

        matches.sort(key=lambda record: record.get("uploaded_at", ""), reverse=True)

        lines = []
        for record in matches[:10]:
            url = await _fresh_url(interaction.guild, record)
            if url is None:
                lines.append(f"• {record['filename']} — _message was deleted_")
            else:
                lines.append(
                    f"• {record['filename']} — {record['size']} — [download]({url})"
                )

        overflow = (
            f"\n_…and {len(matches) - 10} more — narrow the search._"
            if len(matches) > 10
            else ""
        )
        await interaction.edit_original_response(
            content=(
                f"🔗 **{len(matches)} match(es) for `{name}` in #{channel.name}**\n"
                + "\n".join(lines)
                + overflow
            )
        )

    except Exception as e:
        logger.error(f'Error in pdflink command: {str(e)}', exc_info=True)
        await interaction.followup.send(f"Link glitched out. Error: {str(e)}", ephemeral=True)
```

- [ ] **Step 2: Verify the module parses and the suite still passes**

Run: `.venv/bin/python -c "import ast, pathlib; ast.parse(pathlib.Path('app.py').read_text()); print('syntax ok')"`
Expected: `syntax ok`

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: 108 passed

- [ ] **Step 3: Manual verification in Discord**

1. `/pdflink channel:#books name:meditations` → a link that downloads the file.
2. Copy the URL and confirm it carries fresh `ex=` / `is=` / `hm=` query parameters (that is what proves it was re-signed).
3. Delete a message containing a PDF, then `/pdflink` for that filename → `_message was deleted_` on that row, no crash.
4. `/pdflink channel:#books name:zzzz` → ``No PDF matching `zzzz` in #books.``
5. Matching is case-insensitive: `/pdflink channel:#books name:MEDITATIONS` still matches.
6. A filename fragment matching more than 10 files → the `_…and N more_` line appears and exactly 10 links are shown, newest first.

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "feat: add /pdflink command that mints fresh attachment URLs"
```

---

### Task 10: Document the permissions requirement

**Files:**
- Modify: `SETUP_STEPS.md` (the command list in the intro, plus a new numbered section)
- Test: none (documentation)

**Interfaces:**
- Consumes: nothing
- Produces: nothing code-related

- [ ] **Step 1: Add the three commands to the command list**

In `SETUP_STEPS.md`, find this exact line (line 8):

```
`/dearoracle` · `/summarize` · `/sumvideo` · `/detailvideo` · `/finnasumthisup` · `/fryemup` · `/listchannel`
```

Replace it with:

```
`/dearoracle` · `/summarize` · `/sumvideo` · `/detailvideo` · `/finnasumthisup` · `/fryemup` · `/listchannel` · `/pdfs` · `/pdfexport` · `/pdflink`
```

- [ ] **Step 2: Add the permissions section**

Insert this section immediately before the `---` that precedes `## Local testing (optional, before deploying)`:

```markdown
## 5. `/pdfs` · `/pdfexport` · `/pdflink` — extra requirements

These three commands index the PDFs members have uploaded. They **never download
or open the files** — they read attachment metadata (name, size, uploader, date)
and hand out download links on demand.

**Permissions the bot needs, in every channel you want indexed:**

- **View Channel**
- **Read Message History**

A channel missing either is skipped, and the reply lists which ones were skipped —
so a short index usually means a permission is missing, not that the channel is
empty.

**How to grant them:** Server Settings → Roles → the bot's role → enable
**View Channels** and **Read Message History** for the categories and channels
you want covered. Channel-level permissions override role-level ones, so check
the individual channels too.

**Nothing else to configure:** no new secrets, no new environment variables, and
no database. The index is rebuilt from Discord every time you run a command (a
10-minute in-memory cache makes repeat runs instant), so it survives redeploys and
can never go stale.

**Notes**

- Members only ever see channels they can already view. They cannot use these
  commands to discover a locked channel or pull a file out of one.
- Download links Discord gives us expire after about 24 hours. `/pdflink`
  re-fetches the original message to mint a fresh link, so retrieval still works
  months later — as long as the message has not been deleted.
- `/pdfs` with no channel or category scans the whole server and warns up front
  that it may take a while.
- The CSV/JSON from `/pdfexport` includes stable IDs (`channel_id`,
  `message_id`, `attachment_id`) so the community site can build working links
  later.
```

- [ ] **Step 3: Verify the markdown**

Run: `rg -n "^## 5\. " SETUP_STEPS.md`
Expected: one match, `SETUP_STEPS.md:NN:## 5. \`/pdfs\` · ...`

Then open `SETUP_STEPS.md` in the editor and confirm the new section is numbered
correctly, sits before `## Local testing`, and its fenced block closes.

- [ ] **Step 4: Commit**

```bash
git add SETUP_STEPS.md
git commit -m "docs: document PDF index commands and required permissions"
```

---

## Final verification

- [ ] Run the full suite: `.venv/bin/python -m pytest tests/ -v` → 108 passed (65 pre-existing + 43 new).
- [ ] Confirm no dependency drift: `git diff --stat HEAD~10 -- requirements.txt` → empty output.
- [ ] Confirm the new files exist and nothing else changed unexpectedly: `git diff --stat HEAD~10 -- pdf_index.py pdf_scanner.py app.py tests/ SETUP_STEPS.md`
- [ ] Start the bot locally and run all three commands once (manual steps in Tasks 7–9).
- [ ] After deploying to Railway, confirm the logs show `Synced` and that `/pdfs`, `/pdfexport`, and `/pdflink` are registered.

## Spec coverage map

Every requirement in `docs/superpowers/specs/2026-09-19-pdf-library-index-design.md`, and where it is implemented:

| Spec requirement | Task |
|---|---|
| `is_pdf`, `format_size`, `classify_type` | 1 |
| Record shape, `collect_pdf_records`, `annotate_duplicates`, `render_page`, `chunk_records` | 2 |
| `build_pdf_csv`, `build_pdf_json` | 3 |
| `resolve_scope`, channel-beats-category, view-permission filter, `PermissionError`, active threads | 4 |
| `ScanResult`, `scan_channels`, per-channel `Forbidden` isolation, progress callback | 5 |
| `CACHE_TTL`, `clear_cache`, `scan_with_cache` | 6 |
| `/pdfs` (category autocomplete, channel picker, `show_links`, whole-server warning, ephemeral, grouping, pagination, skipped footer) | 7 |
| `/pdfexport` (scope options, CSV + JSON, `<scope>_<YYYYMMDD>` filenames, no-results case) | 8 |
| `/pdflink` (channel + name, up to 10 newest-first, `_fresh_url` re-signing, deleted-message case) | 9 |
| Duplicate `(×N)` marker | 2, 5 |
| Setup/permissions documentation | 10 |
| No new dependencies, no persistence, metadata only | Global Constraints + Final verification |
| Archived threads, RAG, web page, zip/re-upload, AI classification | Out of scope (not implemented, by design) |

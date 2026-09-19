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

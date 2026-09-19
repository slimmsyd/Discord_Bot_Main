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

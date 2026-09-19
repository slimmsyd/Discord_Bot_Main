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

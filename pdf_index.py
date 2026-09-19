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

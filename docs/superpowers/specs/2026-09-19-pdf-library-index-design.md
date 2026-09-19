# `/pdfs` — Server PDF Library Index

**Date:** 2026-09-19
**Status:** Approved (design)
**Files touched:** `pdf_index.py` (new), `pdf_scanner.py` (new), `app.py` (3 commands),
`tests/test_pdf_index.py` (new), `tests/test_pdf_scanner.py` (new),
`SETUP_STEPS.md` (permissions note), `requirements.txt` (unchanged)

## Problem

Members upload PDFs (books, papers, zines) across many channels, and once a
message scrolls out of view the files are effectively lost. There is no way to
answer "what PDFs do we have?", "what kind of books are in here?", or "where is
that one book someone posted last year?" — short of scrolling channels by hand.
Discord search helps only if you already know the exact filename.

## Goal

Three slash commands that let a member:

1. **See an index** of every PDF in a channel, a category, or the whole server,
   labelled with what kind of document it is.
2. **Export that index** as a CSV/JSON manifest, including stable identifiers,
   to feed the community site later.
3. **Retrieve a file later** by getting a fresh, working download link on demand.

The bot never downloads or parses the PDFs. It reads attachment *metadata* only,
and hands the user a link when they want the file.

## Non-Goals

- Reading PDF contents, summarising books, or RAG/"ask the library" features.
- A public web library page (deliberately deferred; see **Out of Scope**).
- Zipping or bulk-downloading files through the bot.
- Persisting an index across restarts (see **Architecture** for why this is a
  feature, not a gap).

## User Experience

All three commands reply **ephemerally** — only the invoker sees the output.

### `/pdfs` — list the index

| Option | Type | Required | Notes |
|--------|------|----------|-------|
| `category` | text, **autocomplete** from `guild.categories` | no | scans every text channel in that category |
| `channel` | native Discord **channel picker** (`discord.TextChannel`) | no | scans that one channel |
| `show_links` | boolean | no (default `false`) | appends a clickable download link per file |

- **`channel` takes precedence over `category`** when both are supplied.
- **Neither supplied → scans every channel in the server.** Because this can be
  slow, the bot immediately defers and edits its message with a warning:
  `⚠️ Scanning the whole server — this may take a while (n/m channels)…`

Output, grouped by channel:

```
📚 12 PDFs — Philosophy
── #philosophy-books ──
• meditations.pdf — 1.2 MB — user#1234 — 2026-01-02
• the-republic.pdf — 3.4 MB — user#5678 — 2026-01-05
── #stoicism ──
• letters-from-a-stoic.pdf — 800 KB — user#1234 — 2026-02-11
```

With `show_links: true` each line gains ` — [download](<signed url>)`.

When the list is too long for one message, it is split into pages with
**Previous / Next** buttons (ephemeral-safe, owned by the invoker).

### `/pdfexport` — download the manifest

Same `category` / `channel` options as `/pdfs`. Replies ephemerally with **two
file attachments** in one message:

- `pdf_index_<scope>_<YYYYMMDD>.csv` — human-readable, one row per PDF.
- `pdf_index_<scope>_<YYYYMMDD>.json` — same data, machine-readable.

`<scope>` is the category name with spaces → dashes, the channel name, or the
literal `all` when neither option was supplied. `<YYYYMMDD>` is the UTC date at
export time.

Both carry the **stable identifiers** (`channel_id`, `message_id`,
`attachment_id`) *and* the fresh signed URL captured at export time. The stable
IDs are what let the community site (or `/pdflink`) mint a new URL months later.

### `/pdflink` — get a file later

| Option | Type | Required | Notes |
|--------|------|----------|-------|
| `channel` | native channel picker | yes | channel to search |
| `name` | text | yes | case-insensitive fragment of the filename |

Scans that one channel, matches filenames by substring, and returns up to **10**
matches, each with a freshly minted link. Prefers the most recent upload when the
fragment is ambiguous. Returns a clear message when nothing matches.

## Why links are regenerated, not stored

Discord attachment URLs are **signed and expire in roughly 24 hours** (the `ex` /
`is` / `hm` query parameters). A stored URL is therefore useless a day later.

Two mechanisms cover this:

1. `/pdfs show_links` and `/pdfexport` hand out URLs captured *during the scan
   that just ran*, so they are fresh by construction.
2. `/pdflink` re-fetches the message (`channel.fetch_message(message_id)`) and
   reads `message.attachments[i].url`, which returns a **newly signed** URL. This
   is the durable retrieval path.

## Architecture

Three units, mirroring the existing `survey_store.py` / `growth_stats.py`
convention: pure logic first, Discord I/O second, thin command glue last.

### Unit A — `pdf_index.py` (pure, no Discord import)

| Function | Does |
|----------|------|
| `is_pdf(attachment)` | `True` if `attachment.content_type == "application/pdf"` **or** `attachment.filename.lower().endswith(".pdf")`. The filename check exists because Discord frequently reports `content_type` as `None`. |
| `collect_pdf_records(messages, channel, category)` | Walks message objects (duck-typed: `.attachments`, `.author`, `.created_at`, `.id`, `.channel.id`), returns a record per PDF attachment. |
| `classify_type(category, channel_name)` | Returns `category` when non-empty, else the channel name with dashes/underscores replaced by spaces and title-cased. |
| `format_size(n_bytes)` | `"800 KB"`, `"1.2 MB"`, etc. |
| `build_pdf_csv(records)` | CSV text; `csv.writer` handles quoting/escaping. Mirrors `build_survey_csv`. |
| `build_pdf_json(records)` | Pretty-printed JSON array. |
| `chunk_records(records, max_chars)` | Splits rendered lines into pages that fit Discord's 2000-char message limit. |
| `render_page(records, *, show_links)` | The per-page text shown to the user. |

**Record shape** (all fields strings except `size_bytes`):

```json
{
  "category": "Philosophy",
  "channel": "philosophy-books",
  "channel_id": "111",
  "filename": "meditations.pdf",
  "type": "Philosophy",
  "size_bytes": 1234567,
  "size": "1.2 MB",
  "uploader": "user#1234",
  "uploader_id": "222",
  "uploaded_at": "2026-01-02T03:04:05+00:00",
  "message_id": "333",
  "attachment_id": "444",
  "url": "https://cdn.discordapp.com/attachments/..."
}
```

### Unit B — `pdf_scanner.py` (async Discord I/O)

- `async def scan_channels(channels, *, on_progress=None) -> ScanResult`
  - For each channel: `async for message in channel.history(limit=None)`, then
    `collect_pdf_records`.
  - Includes each text channel's **active** threads (`channel.threads`) as
    best-effort coverage.
  - Catches `discord.Forbidden` **per channel**, records it in
    `ScanResult.skipped`, and continues. One locked channel never aborts a scan.
  - `on_progress(done, total)` is awaited after each channel so the command can
    edit its message.
- `resolve_scope(guild, *, category=None, channel=None, invoker) -> list[channel]`
  - `channel` wins over `category`; neither → all channels.
  - **Permission filter:** keeps only channels for which
    `channel.permissions_for(invoker).view_channel` is `True`. A user can never
    get links to a channel they cannot open.
  - If an explicitly named `channel` fails that check, raises `PermissionError`
    so the command can refuse with a clear message.
- **Cache:** module-level dict keyed by `(tuple(sorted(channel_ids)),)`, value
  `(monotonic_time, records)`, TTL **600 s**. Exposes `clear_cache()` for tests.
  The cache never persists — it dies with the process, which is intended.

`ScanResult` is a small dataclass: `records: list[dict]` and `skipped: list[str]`
(names of channels the bot could not read).

### Unit C — commands in `app.py`

`/pdfs`, `/pdfexport`, `/pdflink`, plus one autocomplete handler for `category`.
Each follows the existing `defer → work → send` pattern with
`logger.error(..., exc_info=True)` on failure. A shared private helper
`_resolve_and_scan(interaction, category, channel)` keeps the three commands from
duplicating scope logic.

**No new dependencies.** No PDF library is needed because nothing opens the
files. Only `discord.py` and stdlib `csv` / `io` / `json` / `time`.

## Flow

**`/pdfs`**
1. Defer ephemerally.
2. `resolve_scope` → channel list (permission-filtered).
3. All-channels case → edit message with the slow-scan warning.
4. Cache hit within TTL → return immediately.
5. Otherwise scan with a throttled `on_progress` edit (at most one edit per 2 s).
6. Build pages via `chunk_records`; single page → one message, multiple → add
   Prev/Next buttons.
7. Append a footer when `ScanResult.skipped` is non-empty:
   `Skipped 3 channels I can't read: #a, #b, #c`.

**`/pdfexport`** — steps 1–5 as above, then build CSV + JSON and send both as
file attachments. Empty result → plain message, no files.

**`/pdflink`** — resolve the single channel (permission check), scan it, filter
filenames by the `name` fragment, sort newest-first, return up to 10 links. If
the scan cache is warm, reuse it.

## Error Handling

| Situation | Behaviour |
|-----------|-----------|
| Bot lacks **View Channel** / **Read Message History** in a channel | Channel skipped; listed in the footer. Never silent, never fatal. |
| Invoker lacks **View Channel** for a requested channel | Command refuses: `You don't have access to #that-channel.` No scan runs. |
| Scope contains zero PDFs | `No PDFs found in <scope>.` |
| Scan is slow (whole server) | Warning message first, then progress edits. |
| File list exceeds one message | Paginated with Prev/Next buttons. |
| Message deleted between export and `/pdflink` | `discord.NotFound` caught → `That file's message was deleted.` |
| Same filename uploaded more than once | Each occurrence listed; identical filenames in one channel get a `(×N)` count. |
| Discord API error mid-scan | Logged with `exc_info=True`; partial results are still returned for channels that succeeded. |
| Command raises unexpectedly | Caught; friendly failure message, matching other commands. |

## Testing

**`tests/test_pdf_index.py`** (pure, no Discord):
- `is_pdf`: real `content_type`, `None` content_type with `.pdf` name, uppercase
  `.PDF`, a `.docx`, and a `.pdf` with no content_type.
- `collect_pdf_records`: message with 0, 1, and multiple attachments; non-PDF
  attachments excluded; correct channel/category/uploader/date mapping.
- `classify_type`: category present, category `None`, channel name with dashes.
- `format_size`: bytes, KB, MB boundaries.
- `build_pdf_csv`: header order, a filename containing a comma and a quote.
- `chunk_records`: a list that splits at exactly the limit and one that fits in
  a single page.

**`tests/test_pdf_scanner.py`** (async, fake channels):
- A fake async channel yields canned messages → expected records.
- A fake channel raising `discord.Forbidden` is recorded in `skipped` and does
  not prevent other channels from being scanned.
- `resolve_scope`: channel beats category; invoker-invisible channels filtered;
  explicit inaccessible channel raises `PermissionError`.
- Cache: second call within TTL makes no additional `history()` calls; after
  `clear_cache()` it rescans.

**Manual verification** in the test server: `/pdfs channel:#books`,
`/pdfs category:Philosophy`, `/pdfs` (whole server), `/pdfexport`, `/pdflink`,
plus a locked channel to confirm the skip footer.

## Setup / permissions

Documented in `SETUP_STEPS.md`:

- Bot needs **View Channel** and **Read Message History** in every scanned channel.
- Nothing else changes: no new environment variables, no new secrets, no new
  dependencies, no database. Redeploys are safe because nothing is persisted.

## Out of Scope (YAGNI)

- **Reading PDF text / RAG / auto-summaries** — explicitly not wanted.
- **Archived threads and archived forum posts** — they need a separate paginated
  API; v1 covers text channels plus their *active* threads.
- **Persisting the index** — Discord messages are the source of truth and are
  re-derivable, so a stored copy only adds staleness and Railway's ephemeral-disk
  failure mode. Supabase can be introduced in a later phase if the community site
  needs a server-side copy; only the storage layer would change.
- **Web library page / public API** — deferred to a later phase.
- **Downloading, zipping, or re-uploading files** — users get links instead;
  Discord's upload size limits would otherwise truncate the feature.
- **OpenAI/DeepSeek classification of document type** — the category/channel
  already answers "what kind of book is this" at zero cost and zero latency.
- **Configurable scan depth (date/size filters)** — full history per channel,
  with the cache absorbing the cost of repeat calls.

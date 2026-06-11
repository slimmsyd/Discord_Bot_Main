"""Member-export helpers, kept free of bot/env dependencies so they're testable.

`build_member_csv` turns a list of plain member dicts into CSV text; the caller
(app.py) is responsible for pulling those dicts off live discord.Member objects.
Keeping the formatting pure means we can test column order and escaping without
a running bot.
"""

import csv
import io

# Column order for the exported CSV. Any key missing from a row renders blank,
# and any extra key on a row is ignored (extrasaction="ignore").
MEMBER_CSV_FIELDS = [
    "user_id",
    "username",
    "global_name",
    "server_nick",
    "tag",
    "is_bot",
    "account_created_utc",
    "joined_at_utc",
    "premium_since_utc",
    "pending",
    "roles",
    "join_method",
    "join_invite_code",
    "inviter_tag",
]


def build_member_csv(rows, fields=MEMBER_CSV_FIELDS):
    """Build CSV text (with header) from a list of member dicts.

    Missing keys render as blank cells; unknown keys are dropped. csv handles
    quoting/escaping so commas or newlines in nicknames don't break columns.
    """
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, "") for k in fields})
    return buf.getvalue()


def member_export_filename(guild_name, stamp):
    """A filesystem-safe CSV filename like ``members_My_Server_20260611_140700.csv``."""
    safe = "".join(c if c.isalnum() else "_" for c in (guild_name or "")).strip("_")
    return f"members_{safe or 'guild'}_{stamp}.csv"

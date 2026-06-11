import csv
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from member_export import build_member_csv, member_export_filename, MEMBER_CSV_FIELDS


def _parse(csv_text):
    return list(csv.DictReader(io.StringIO(csv_text)))


def test_header_matches_fields_even_with_no_rows():
    csv_text = build_member_csv([])
    header = csv_text.splitlines()[0].split(",")
    assert header == MEMBER_CSV_FIELDS


def test_missing_keys_render_blank():
    rows = _parse(build_member_csv([{"user_id": "123", "username": "neo"}]))
    assert rows[0]["user_id"] == "123"
    assert rows[0]["username"] == "neo"
    assert rows[0]["roles"] == ""  # absent key -> blank, not a crash


def test_unknown_keys_are_ignored():
    # extrasaction="ignore" means a stray key doesn't blow up the writer.
    csv_text = build_member_csv([{"user_id": "1", "not_a_column": "x"}])
    assert "not_a_column" not in csv_text.splitlines()[0]


def test_commas_and_newlines_are_escaped_losslessly():
    rows = _parse(build_member_csv([{"server_nick": "Smith, Agent\nLine2", "roles": "a;b"}]))
    assert rows[0]["server_nick"] == "Smith, Agent\nLine2"
    assert rows[0]["roles"] == "a;b"


def test_filename_is_sanitized():
    assert member_export_filename("My Server!", "20260611_140700") == "members_My_Server_20260611_140700.csv"


def test_filename_falls_back_when_empty():
    assert member_export_filename("", "20260611_140700") == "members_guild_20260611_140700.csv"

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from discord_utils import split_for_discord


def test_short_text_is_single_chunk():
    assert split_for_discord("hello") == ["hello"]


def test_text_at_limit_is_single_chunk():
    text = "a" * 1900
    assert split_for_discord(text) == [text]


def test_long_text_splits_into_multiple_chunks():
    text = "a" * 4000
    chunks = split_for_discord(text)
    assert len(chunks) == 3
    assert all(len(c) <= 1900 for c in chunks)
    assert "".join(chunks) == text  # lossless: chunks reassemble the original


def test_just_over_limit_splits_into_two():
    text = "a" * 1901
    chunks = split_for_discord(text)
    assert len(chunks) == 2
    assert "".join(chunks) == text

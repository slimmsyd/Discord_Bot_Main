import sys
from pathlib import Path

# Allow importing channel_finder.py from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from channel_finder import fuzzy_match

CHANNELS = [
    {"id": 111, "name": "announcements", "topic": "official updates"},
    {"id": 222, "name": "general", "topic": "main chat"},
    {"id": 333, "name": "newsletter", "topic": "weekly Substack drops & email digests"},
    {"id": 444, "name": "defi", "topic": "yield farming, lending protocols"},
]


def test_fuzzy_match_finds_term_in_topic():
    # "Substack" is not in any name, but it is in #newsletter's topic.
    results = fuzzy_match("Substack", CHANNELS)
    assert results[0]["id"] == 333


def test_fuzzy_match_handles_typo_in_name():
    results = fuzzy_match("newsleter", CHANNELS)  # missing a 't'
    assert results[0]["id"] == 333


def test_fuzzy_match_returns_empty_for_conceptual_query():
    # No literal "crypto" anywhere -> local matching cannot find it.
    assert fuzzy_match("Crypto", CHANNELS) == []


def test_fuzzy_match_respects_limit():
    results = fuzzy_match("general", CHANNELS, limit=1)
    assert len(results) == 1

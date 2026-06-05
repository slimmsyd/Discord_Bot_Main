import sys
from pathlib import Path

# Allow importing channel_finder.py from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from channel_finder import fuzzy_match, rank_channels

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


# --- Stub DeepSeek client: mimics client.chat.completions.create(...) ---

class _StubMessage:
    def __init__(self, content):
        self.content = content


class _StubChoice:
    def __init__(self, content):
        self.message = _StubMessage(content)


class _StubResponse:
    def __init__(self, content):
        self.choices = [_StubChoice(content)]


class _Completions:
    def __init__(self, content, error):
        self._content = content
        self._error = error

    def create(self, **kwargs):
        if self._error:
            raise self._error
        return _StubResponse(self._content)


class _Chat:
    def __init__(self, content, error):
        self.completions = _Completions(content, error)


class StubClient:
    """Stand-in for the OpenAI-compatible DeepSeek client."""
    def __init__(self, content=None, error=None):
        self.chat = _Chat(content, error)


def test_rank_channels_returns_ai_picks_in_order():
    client = StubClient(
        content='{"matches": [{"id": 333, "reason": "substack drops here"}, '
                '{"id": 444, "reason": "defi protocols"}]}'
    )
    results = rank_channels("Crypto", CHANNELS, client, "deepseek-chat")
    assert [r["id"] for r in results] == [333, 444]
    assert results[0]["reason"] == "substack drops here"


def test_rank_channels_drops_hallucinated_ids():
    client = StubClient(
        content='{"matches": [{"id": 999, "reason": "made up"}, '
                '{"id": 444, "reason": "real one"}]}'
    )
    results = rank_channels("defi", CHANNELS, client, "deepseek-chat")
    assert [r["id"] for r in results] == [444]


def test_rank_channels_returns_empty_on_client_error():
    client = StubClient(error=RuntimeError("api down"))
    assert rank_channels("Crypto", CHANNELS, client, "deepseek-chat") == []


def test_rank_channels_returns_empty_on_bad_json():
    client = StubClient(content="this is not json")
    assert rank_channels("Crypto", CHANNELS, client, "deepseek-chat") == []

# /listchannel Semantic Channel Finder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/listchannel name:<term>` slash command that returns the 2 most relevant channels (by meaning) as clickable links — public on a hit, private on a miss.

**Architecture:** Matching logic lives in a new dependency-free `channel_finder.py` module (AI ranking via DeepSeek + a fuzzy fallback), so it is unit-testable without booting the Discord bot or needing live tokens. `app.py` gets one thin `@bot.tree.command` wrapper that gathers permission-filtered channels, calls the module, and handles Discord I/O + mixed visibility.

**Tech Stack:** Python, discord.py 2.x, DeepSeek (OpenAI-compatible client, already configured as `deepseek_client` / `AI_MODEL` in `app.py`), pytest 9.

Spec: `docs/superpowers/specs/2026-06-05-listchannel-semantic-finder-design.md`

---

## File Structure

- **Create `channel_finder.py`** (repo root) — pure matching logic. No `discord`, no env, no I/O beyond the injected client. Functions: `_truncate`, `fuzzy_match`, `rank_channels`, `find_channels`.
- **Create `tests/test_channel_finder.py`** — unit tests with a stubbed DeepSeek client.
- **Modify `app.py`** — add `from channel_finder import find_channels` to the import block, and add the `listchannel` command after the `fryemup` command (just before the `@bot.event` `on_command_error` block near line 790).

Run tests with: `python3 -m pytest tests/test_channel_finder.py -v`

---

## Task 1: `fuzzy_match` fallback (local string matching)

**Files:**
- Create: `channel_finder.py`
- Test: `tests/test_channel_finder.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_channel_finder.py`:

```python
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
    assert len(results) <= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_channel_finder.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'channel_finder'`

- [ ] **Step 3: Write minimal implementation**

Create `channel_finder.py`:

```python
"""Semantic + fuzzy channel matching for the /listchannel command.

Deliberately free of Discord and environment dependencies so the ranking logic
can be unit-tested with a stubbed DeepSeek client (no live bot/token needed).
"""

import json
import logging
from difflib import SequenceMatcher

logger = logging.getLogger('discord_bot')

# How many characters of a channel topic to include in the AI prompt.
TOPIC_TRUNCATE = 120


def _truncate(text, limit=TOPIC_TRUNCATE):
    if not text:
        return ""
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def fuzzy_match(name, channels, limit=2):
    """Local fallback: rank channels by literal name/topic similarity.

    channels: list of {"id": int, "name": str, "topic": str|None}
    Returns: list of {"id": int, "reason": str}, best first, up to `limit`.
    """
    query = name.lower().strip()
    scored = []
    for ch in channels:
        ch_name = (ch.get("name") or "").lower()
        ch_topic = (ch.get("topic") or "").lower()
        score = 0.0
        if query in ch_name:
            score += 2.0
        if query in ch_topic:
            score += 1.0
        # Catch typos / partial overlap on the name.
        score += SequenceMatcher(None, query, ch_name).ratio()
        if score > 0.5:
            scored.append((score, ch))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [{"id": ch["id"], "reason": "name/topic match"} for _, ch in scored[:limit]]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_channel_finder.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add channel_finder.py tests/test_channel_finder.py
git commit -m "feat: add fuzzy_match fallback for channel finder"
```

---

## Task 2: `rank_channels` (AI semantic ranking)

**Files:**
- Modify: `channel_finder.py`
- Test: `tests/test_channel_finder.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_channel_finder.py` (add `rank_channels` to the existing import line so it reads `from channel_finder import fuzzy_match, rank_channels`):

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_channel_finder.py -v`
Expected: FAIL — `ImportError: cannot import name 'rank_channels'`

- [ ] **Step 3: Write minimal implementation**

Add to `channel_finder.py` (after `fuzzy_match`):

```python
def rank_channels(name, channels, client, model, limit=2):
    """AI ranking via a DeepSeek (OpenAI-compatible) client.

    Returns list of {"id": int, "reason": str} for the best matches, validated
    against the input channel IDs. Returns [] on any error or if nothing valid
    comes back, signalling the caller to fall back to fuzzy_match.
    """
    if not channels:
        return []
    valid_ids = {int(ch["id"]) for ch in channels}
    catalog = [
        {"id": ch["id"], "name": ch.get("name", ""), "topic": _truncate(ch.get("topic"))}
        for ch in channels
    ]
    system = (
        "You help members find the right Discord channel. Given a search term and "
        "a catalog of channels (id, name, topic), pick the channels whose PURPOSE "
        "best matches the term by meaning, not just literal wording. "
        f"Return at most {limit}, best first. "
        'Respond ONLY as JSON: {"matches": [{"id": <channel id>, "reason": "<short reason>"}]}. '
        "If nothing fits, return an empty matches list."
    )
    user = f'Search term: "{name}"\nChannels:\n{json.dumps(catalog)}'
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            max_tokens=300,
            temperature=0.2,
        )
        data = json.loads(response.choices[0].message.content)
    except Exception as e:
        logger.error(f"rank_channels AI call failed: {e}", exc_info=True)
        return []

    results = []
    seen = set()
    for match in data.get("matches", []):
        try:
            cid = int(match["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if cid in valid_ids and cid not in seen:
            seen.add(cid)
            reason = str(match.get("reason", "")).strip() or "relevant match"
            results.append({"id": cid, "reason": reason})
        if len(results) >= limit:
            break
    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_channel_finder.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add channel_finder.py tests/test_channel_finder.py
git commit -m "feat: add rank_channels AI semantic ranking"
```

---

## Task 3: `find_channels` orchestrator (AI with fuzzy fallback)

**Files:**
- Modify: `channel_finder.py`
- Test: `tests/test_channel_finder.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_channel_finder.py` (add `find_channels` to the import line: `from channel_finder import fuzzy_match, rank_channels, find_channels`):

```python
def test_find_channels_uses_ai_when_available():
    client = StubClient(content='{"matches": [{"id": 333, "reason": "ai pick"}]}')
    results = find_channels("anything", CHANNELS, client, "deepseek-chat")
    assert results == [{"id": 333, "reason": "ai pick"}]


def test_find_channels_falls_back_to_fuzzy_on_ai_failure():
    client = StubClient(error=RuntimeError("api down"))
    # AI returns [] -> fuzzy finds "Substack" in #newsletter's topic.
    results = find_channels("Substack", CHANNELS, client, "deepseek-chat")
    assert results[0]["id"] == 333


def test_find_channels_returns_empty_when_nothing_matches():
    client = StubClient(content='{"matches": []}')
    # AI empty, fuzzy empty (no literal "crypto") -> overall empty.
    assert find_channels("Crypto", CHANNELS, client, "deepseek-chat") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_channel_finder.py -v`
Expected: FAIL — `ImportError: cannot import name 'find_channels'`

- [ ] **Step 3: Write minimal implementation**

Add to `channel_finder.py` (after `rank_channels`):

```python
def find_channels(name, channels, client, model, limit=2):
    """Try AI ranking; fall back to fuzzy matching if it yields nothing."""
    results = rank_channels(name, channels, client, model, limit=limit)
    if results:
        return results
    return fuzzy_match(name, channels, limit=limit)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_channel_finder.py -v`
Expected: PASS (11 passed)

- [ ] **Step 5: Commit**

```bash
git add channel_finder.py tests/test_channel_finder.py
git commit -m "feat: add find_channels orchestrator with fuzzy fallback"
```

---

## Task 4: Wire `/listchannel` command into `app.py`

**Files:**
- Modify: `app.py` (import block near line 17; new command before `on_command_error` near line 790)

This task is the Discord glue. It is verified manually in a test server (the
unit-tested logic lives in `channel_finder.py`); mocking `discord.Interaction`
adds no real coverage.

- [ ] **Step 1: Add the import**

In `app.py`, after the existing import block (after line 17, `import httpx`), add:

```python
from channel_finder import find_channels
```

- [ ] **Step 2: Add the command**

In `app.py`, immediately after the end of the `fryemup` command function and
before the `@bot.event` `async def on_command_error` block (near line 790),
insert:

```python
@bot.tree.command(name="listchannel", description="Find the most relevant channel(s) by meaning")
@discord.app_commands.describe(name="What you're looking for, e.g. Substack or Crypto")
async def listchannel(interaction: discord.Interaction, name: str):
    logger.info(f'listchannel search from {interaction.user} in {interaction.guild.name}: {name}')

    try:
        # Private "thinking" indicator; a no-match leaves no public trace.
        await interaction.response.defer(ephemeral=True)

        # Gather text-based channels the invoking user can actually view.
        candidates = []
        for ch in interaction.guild.channels:
            if not isinstance(ch, (discord.TextChannel, discord.ForumChannel)):
                continue
            if not ch.permissions_for(interaction.user).view_channel:
                continue
            candidates.append({
                "id": ch.id,
                "name": ch.name,
                "topic": getattr(ch, "topic", None),
            })

        matches = find_channels(name, candidates, deepseek_client, AI_MODEL, limit=2)

        if not matches:
            await interaction.followup.send(
                f'Couldn\'t find a channel matching "{name}" 🤷\n'
                "Try a broader term, or it may not exist yet.",
                ephemeral=True,
            )
            return

        lines = [f'🔎 Top matches for "{name}":']
        for i, m in enumerate(matches, start=1):
            lines.append(f"{i}. <#{m['id']}> — {m['reason']}")

        # Public message so anyone in the channel can use the links.
        await interaction.channel.send("\n".join(lines))
        # Private confirmation closes out the ephemeral defer for the invoker.
        await interaction.followup.send("Posted the matches above 👆", ephemeral=True)

    except Exception as e:
        logger.error(f'Error in listchannel command: {str(e)}', exc_info=True)
        await interaction.followup.send(
            f"Ay {interaction.user.mention}, channel search glitched out. Try again! Error: {str(e)}",
            ephemeral=True,
        )
```

- [ ] **Step 3: Verify the file imports cleanly (syntax check)**

Run: `python3 -c "import ast; ast.parse(open('app.py').read()); print('app.py parses OK')"`
Expected: `app.py parses OK`

(A full `import app` requires live `DISCORD_BOT_TOKEN` / `DEEPSEEK_API_KEY` and
starts bot setup, so an AST parse is the right syntax check here.)

- [ ] **Step 4: Manual verification in a test server**

Start the bot (`python3 app.py` with valid `.env`), wait for "Slash commands
synced", then in Discord:
- `/listchannel name:Substack` → posts a **public** message with up to 2
  clickable `#channel` links; clicking one jumps to that channel.
- `/listchannel name:<gibberish>` → replies **privately** with the "couldn't
  find" message (no public post).
- As a non-admin user, confirm a channel you cannot view never appears in results.

- [ ] **Step 5: Commit**

```bash
git add app.py
git commit -m "feat: add /listchannel semantic channel finder command"
```

---

## Self-Review Notes

- **Spec coverage:** semantic match (Task 2), top-2 (limit=2 throughout), clickable `<#id>` links (Task 4), public hit / private miss (Task 4), permission filter (Task 4), fuzzy fallback (Tasks 1+3), `name` option (Task 4) — all covered.
- **Type consistency:** every function returns `list[{"id": int, "reason": str}]`; `find_channels`/`rank_channels`/`fuzzy_match` share the same `(name, channels, [client, model,] limit)` shape; the command consumes `m["id"]` / `m["reason"]` exactly as produced.
- **No placeholders:** all code blocks are complete and runnable.

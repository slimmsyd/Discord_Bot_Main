# `/listchannel` — Semantic Channel Finder

**Date:** 2026-06-05
**Status:** Approved (design)
**File touched:** `app.py` (single new command + one helper)

## Problem

The server has so many channels that members can't reliably find the one they
want. Plain Discord search matches channel names literally, so a member looking
for "Crypto" misses `#defi` / `#altcoins`, and someone looking for "Substack"
misses `#newsletter`. The result: "sometimes I just can't find a channel I know
exists."

## Goal

A slash command that takes a free-text query and returns the **2 most relevant
channels** as clickable links that jump the user to the channel, matching by
*meaning* rather than exact text.

## User Experience

```
/listchannel query:Crypto
```

Bot replies **publicly** in the channel (so any member can use the links too):

```
🔎 Top matches for "Crypto":
1. <#123456789>  — main hub for token discussion & prices
2. <#987654321>  — DeFi protocols and yield strategies
```

`<#channel_id>` renders as a clickable `#channel-name` link in Discord that
jumps the user straight to that channel.

### Notes / constraints
- Discord requires slash command names to be **lowercase** → command is
  `/listchannel` (not `/listChannel`). The `query` argument is free-form.
- Reply is **public** (not ephemeral) — posted with `interaction.followup.send()`
  with no `ephemeral=True`.
- Returns **up to 2** matches to stay concise.

## Flow

1. `await interaction.response.defer()` — public defer (gives time for the AI
   call; no `ephemeral` flag).
2. **Gather candidate channels** from `interaction.guild.channels`:
   - Keep text-based types only: `discord.TextChannel`, `discord.ForumChannel`
     (announcement channels are `TextChannel` subclasses, so included).
   - Keep only channels the **invoking user can view**:
     `channel.permissions_for(interaction.user).view_channel is True`.
     This prevents pointing a user at a channel they can't open.
3. **Rank with DeepSeek** via the existing `deepseek_client`:
   - Build a compact catalog: for each channel an entry with its `id`, `name`,
     and `topic` truncated to ~120 chars (topic may be `None`).
   - Prompt asks the model to return the 2 best-matching channel IDs as JSON,
     ranked best-first, plus a short reason for each.
   - Low temperature (e.g. `0.2`) for deterministic ranking. `model = AI_MODEL`.
4. **Validate & reply**:
   - Parse JSON; keep only IDs that exist in the candidate set (drop any the
     model invents/hallucinates).
   - Format the public message with `<#id>` mentions + reason, best first.

## Architecture

Two units, separated so the ranking logic is testable without Discord:

### Unit A — `rank_channels(query, channels, *, limit=2) -> list[dict]`
- **Does:** Given a query string and a list of channel descriptors
  (`{"id", "name", "topic"}`), calls DeepSeek and returns an ordered list of
  `{"id", "reason"}` for the top matches, validated against the input IDs.
- **Depends on:** `deepseek_client`, `AI_MODEL`.
- **Pure-ish & testable:** Takes plain dicts (not Discord objects) so it can be
  unit-tested with a stubbed DeepSeek client. Does no Discord I/O.

### Unit B — `@bot.tree.command listchannel(interaction, query)`
- **Does:** The Discord wrapper. Gathers/filters channels into descriptor
  dicts, calls `rank_channels`, maps results back to channel IDs, formats and
  sends the public reply.
- **Depends on:** Unit A, `discord`, the bot tree.
- Mirrors the existing command pattern (`defer → work → followup.send`,
  try/except with a friendly error message + `logger.error(..., exc_info=True)`).

## Error Handling

| Situation | Behavior |
|-----------|----------|
| DeepSeek errors / response not valid JSON / no valid IDs returned | **Fall back** to local fuzzy name matching (substring + simple ratio on `name`/`topic`); return top 2. Guarantees an answer even if the AI call fails. |
| No channels match at all (even fallback empty) | Friendly message: `Couldn't find a channel matching "<query>" 🤷` |
| Command raises unexpectedly | Caught by the command's try/except; logs with `exc_info=True` and sends a friendly failure message, matching other commands. |
| Guild has many channels | Names/topics are short; full catalog of names + truncated topics fits comfortably in one prompt. No pagination needed for typical server sizes. |

## Testing

- **Unit test `rank_channels`** with a stubbed DeepSeek client:
  - returns the 2 IDs the model picked, in order;
  - drops hallucinated IDs not in the input set;
  - returns `[]` (triggering fallback) on malformed JSON.
- **Unit test the fuzzy fallback** independently: given channels + query, returns
  expected top-2 by name/topic similarity.
- The Discord wrapper (Unit B) is thin glue; verified manually in a test server
  (`/listchannel query:Crypto` returns clickable links; locked channels are
  excluded for a non-privileged user).

## Out of Scope (YAGNI)

- Embeddings / vector store — a single ranking prompt is enough at this scale.
- Caching channel lists — channels are cheap to read from `guild.channels`.
- Searching voice/stage/category channels or threads — text + forum only.
- Configurable result count — fixed at 2 per the approved design.

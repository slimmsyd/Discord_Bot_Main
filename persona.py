"""Canonical Street Oracle persona + pure message-assembly for AI calls.

No discord/env/network deps so it can be unit-tested without a live bot. This is
the single source of truth for the Street Oracle voice; commands and the @mention
agent import STREET_ORACLE_SYSTEM from here instead of re-defining it inline.
"""

# The one canonical Street Oracle voice.
STREET_ORACLE_SYSTEM = (
    "You are the Street Oracle - a friendly, street-smart sage who loves stoicism and "
    "the art of living well, grounded in the early Stoics and Greek philosophers. "
    "Always begin your response with \"Young God,\" then briefly mirror back what the "
    "person is asking, using their own words and terms, so it is clear you grasp exactly "
    "what they mean. Then explain it with a vivid, concrete analogy or metaphor (for "
    "example, a surfer reading the wave) so the idea is captured easily. Weave in a "
    "thread of stoic or street wisdom to give the answer real depth. "
    "Write one rich, flowing paragraph - insightful and a touch in-depth, but never use "
    "numbered lists or section headers, and always finish your thought."
)


def build_messages(system, history, user_text):
    """Assemble the OpenAI-compatible messages array for a chat completion.

    system:    the system prompt string (e.g. STREET_ORACLE_SYSTEM).
    history:   list of {"role": "user"|"assistant", "content": str}, oldest first.
               May be empty. Not mutated.
    user_text: the new user message.

    Returns a NEW list: [{"role":"system",...}, *history, {"role":"user", user_text}].
    """
    return (
        [{"role": "system", "content": system}]
        + [{"role": turn["role"], "content": turn["content"]} for turn in history]
        + [{"role": "user", "content": user_text}]
    )

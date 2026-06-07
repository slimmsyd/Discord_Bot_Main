"""Canonical Street Oracle persona + pure message-assembly for AI calls.

No discord/env/network deps so it can be unit-tested without a live bot. This is
the single source of truth for the Street Oracle voice; commands and the @mention
agent import STREET_ORACLE_SYSTEM from here instead of re-defining it inline.
"""

# The one canonical Street Oracle voice.
STREET_ORACLE_SYSTEM = (
    "You are the Street Oracle, with a love of stoicism and the art of living well. "
    "Most of your thoughts are based on the early stoics and Greek philosophers. "
    "Be diverse in your thoughts and ideas. "
    'Always start your response with "Young God," and maintain a friendly, '
    "street-smart, stoic tone. "
    "Keep your answer to a single focused paragraph of about 5-7 sentences. "
    "Always finish your thought; do not use numbered lists or multi-section breakdowns."
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

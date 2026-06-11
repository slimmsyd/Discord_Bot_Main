"""AI survey-question generation.

`parse_questions` is pure (string -> list of clean question strings) so the
fragile bit — coping with whatever shape the model returns — is fully testable.
`generate_questions` is the thin wrapper that actually calls the LLM, mirroring
how channel_finder takes an injected client + model.

Discord caps a modal TextInput label at 45 characters, so questions are trimmed
to fit; the LLM is also asked to keep them short.
"""

import json
import re

QUESTION_MAX_LEN = 45  # Discord TextInput label limit


def parse_questions(text, n):
    """Extract up to `n` clean question strings from a model response.

    Handles a JSON array (preferred), a fenced code block wrapping one, or a
    plain numbered/bulleted list as a fallback. Each question is trimmed to
    QUESTION_MAX_LEN so it's a valid modal label.
    """
    if not text:
        return []
    text = text.strip()

    # Strip a ```json ... ``` fence if present.
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()

    questions = []
    try:
        data = json.loads(text)
        if isinstance(data, list):
            questions = [str(q).strip() for q in data if str(q).strip()]
    except (json.JSONDecodeError, ValueError):
        # Fallback: one question per line, stripping "1.", "-", "*" prefixes.
        for line in text.splitlines():
            line = re.sub(r"^[\s\-\*\d\.\)]+", "", line.strip()).strip()
            # Drop obvious wrapper lines like "Here are the questions:".
            if line and not line.lower().endswith(":"):
                questions.append(line)

    cleaned = []
    for q in questions[:n]:
        if len(q) > QUESTION_MAX_LEN:
            q = q[:QUESTION_MAX_LEN].rstrip()
        cleaned.append(q)
    return cleaned


def generate_questions(topic, n, client, model):
    """Ask the LLM for `n` concise survey questions about `topic`."""
    prompt = (
        f"Generate exactly {n} short survey questions to learn about: {topic}. "
        f"Each question must be a clear prompt of at most {QUESTION_MAX_LEN} characters. "
        "Return ONLY a JSON array of strings — no preamble, no numbering, no extra text."
    )
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You write concise survey questions. Output only a JSON array of strings."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=400,
        temperature=0.5,
    )
    return parse_questions(response.choices[0].message.content, n)

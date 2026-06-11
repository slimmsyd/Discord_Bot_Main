import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from survey_ai import parse_questions, QUESTION_MAX_LEN


def test_parses_plain_json_array():
    out = parse_questions('["What tools do you use?", "How often?"]', 5)
    assert out == ["What tools do you use?", "How often?"]


def test_strips_code_fence():
    text = '```json\n["A?", "B?"]\n```'
    assert parse_questions(text, 5) == ["A?", "B?"]


def test_falls_back_to_numbered_list():
    text = "1. What tools do you use?\n2. How often?\n3. Why?"
    assert parse_questions(text, 5) == ["What tools do you use?", "How often?", "Why?"]


def test_fallback_drops_wrapper_label_line():
    text = "Here are your questions:\n- What tools?\n- Why them?"
    assert parse_questions(text, 5) == ["What tools?", "Why them?"]


def test_caps_count_to_n():
    assert parse_questions('["a","b","c","d"]', 2) == ["a", "b"]


def test_truncates_long_question_to_label_limit():
    long_q = "x" * 80
    out = parse_questions(f'["{long_q}"]', 1)
    assert len(out[0]) == QUESTION_MAX_LEN


def test_empty_input_returns_empty():
    assert parse_questions("", 5) == []
    assert parse_questions(None, 5) == []

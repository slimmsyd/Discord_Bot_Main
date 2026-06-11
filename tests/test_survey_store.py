import csv
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from survey_store import SurveyStore, build_survey_csv


SURVEY = {"id": "abc123", "topic": "tools", "questions": ["Which tools?", "How often?"], "active": True}


def test_build_csv_has_question_columns():
    responses = [
        {"user_id": "1", "username": "neo", "submitted_at": "2026-06-11", "answers": ["VSCode", "Daily"]},
    ]
    rows = list(csv.reader(io.StringIO(build_survey_csv(SURVEY, responses))))
    assert rows[0] == ["user_id", "username", "submitted_at", "Q1: Which tools?", "Q2: How often?"]
    assert rows[1] == ["1", "neo", "2026-06-11", "VSCode", "Daily"]


def test_build_csv_pads_missing_answers():
    responses = [{"user_id": "2", "username": "trin", "submitted_at": "x", "answers": ["only one"]}]
    rows = list(csv.reader(io.StringIO(build_survey_csv(SURVEY, responses))))
    assert rows[1] == ["2", "trin", "x", "only one", ""]  # 2nd question left blank


def test_store_create_get_and_responses(tmp_path):
    store = SurveyStore(str(tmp_path / "surveys.json"))
    store.create(dict(SURVEY))
    store.add_response("abc123", {"user_id": "1", "answers": ["a", "b"]})
    store.add_response("abc123", {"user_id": "2", "answers": ["c", "d"]})

    reloaded = SurveyStore(str(tmp_path / "surveys.json"))
    assert reloaded.get("abc123")["topic"] == "tools"
    assert len(reloaded.responses("abc123")) == 2


def test_list_active_and_close(tmp_path):
    store = SurveyStore(str(tmp_path / "surveys.json"))
    store.create({"id": "a", "topic": "t1", "questions": [], "created_at": "2026-01-01", "active": True})
    store.create({"id": "b", "topic": "t2", "questions": [], "created_at": "2026-02-01", "active": True})
    assert {s["id"] for s in store.list_active()} == {"a", "b"}
    store.close("a")
    assert {s["id"] for s in store.list_active()} == {"b"}


def test_latest_picks_most_recent(tmp_path):
    store = SurveyStore(str(tmp_path / "surveys.json"))
    store.create({"id": "a", "topic": "old", "questions": [], "created_at": "2026-01-01"})
    store.create({"id": "b", "topic": "new", "questions": [], "created_at": "2026-05-01"})
    assert store.latest()["id"] == "b"


def test_latest_none_when_empty(tmp_path):
    assert SurveyStore(str(tmp_path / "surveys.json")).latest() is None

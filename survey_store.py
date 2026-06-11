"""Persisted survey definitions + responses, JSON-backed.

File shape:
    {
      "surveys":   { survey_id: {id, topic, questions, channel_id, message_id,
                                 created_by, created_at, active} },
      "responses": { survey_id: [ {user_id, username, answers, submitted_at} ] }
    }

`build_survey_csv` is pure (survey + responses -> CSV text) so it's testable
without any disk or discord involvement.
"""

import csv
import io
import json
import os


def survey_message_text(survey, count, closed=False):
    """The text of the posted survey message, including a live response count."""
    topic = survey.get("topic", "Survey")
    n = len(survey.get("questions", []))
    if closed:
        return f"📋 **Survey: {topic}** — _closed_\n💬 {count} response(s) collected."
    tail = f"\n💬 {count} response(s) so far." if count else ""
    return f"📋 **Survey: {topic}**\nClick below to answer — {n} quick questions.{tail}"


def build_survey_csv(survey, responses):
    """CSV with one column per question. Answers are positional (answers[i] maps
    to questions[i]); short/long answer lists are padded/truncated to fit."""
    questions = survey.get("questions", [])
    header = ["user_id", "username", "submitted_at"] + [
        f"Q{i + 1}: {q}" for i, q in enumerate(questions)
    ]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    for r in responses:
        answers = r.get("answers", [])
        row = [r.get("user_id", ""), r.get("username", ""), r.get("submitted_at", "")]
        for i in range(len(questions)):
            row.append(answers[i] if i < len(answers) else "")
        writer.writerow(row)
    return buf.getvalue()


class SurveyStore:
    """JSON-backed store for surveys and their responses (atomic writes)."""

    def __init__(self, path):
        self.path = path
        self._data = {"surveys": {}, "responses": {}}
        self.load()

    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                self._data = {
                    "surveys": loaded.get("surveys", {}),
                    "responses": loaded.get("responses", {}),
                }
            except (json.JSONDecodeError, OSError):
                self._data = {"surveys": {}, "responses": {}}

    def save(self):
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)
        os.replace(tmp, self.path)

    def create(self, survey):
        """Store a new survey definition (keyed by survey['id'])."""
        self._data["surveys"][survey["id"]] = survey
        self._data["responses"].setdefault(survey["id"], [])
        self.save()

    def get(self, survey_id):
        return self._data["surveys"].get(survey_id)

    def set_message(self, survey_id, message_id):
        """Record the posted message id so the survey can be located/closed."""
        s = self._data["surveys"].get(survey_id)
        if s is not None:
            s["message_id"] = message_id
            self.save()

    def close(self, survey_id):
        s = self._data["surveys"].get(survey_id)
        if s is not None:
            s["active"] = False
            self.save()

    def add_response(self, survey_id, response):
        self._data["responses"].setdefault(survey_id, []).append(response)
        self.save()

    def responses(self, survey_id):
        return list(self._data["responses"].get(survey_id, []))

    def list_active(self):
        return [s for s in self._data["surveys"].values() if s.get("active", True)]

    def latest(self):
        """The most recently created survey, or None."""
        surveys = list(self._data["surveys"].values())
        if not surveys:
            return None
        return max(surveys, key=lambda s: s.get("created_at", ""))

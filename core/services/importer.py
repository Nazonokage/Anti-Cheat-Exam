"""Imports the classroom JSON questionnaire format into Exam/Question/Choice rows.

Expected JSON shape (see data/sampletopic.json):
{
  "subject": "...",
  "title": "...",
  "secondsPerQuestion": 60,
  "hintsEnabled": true,
  "multipleChoice": [{"id","text","options":[...], "answerIndex", "hint"}],
  "boolean": [{"id","text","answer": true/false, "hint"}],
  "identification": [{"id","text","answer", "hint"}]
}
"""
import json

from core.models import Exam, Question, Choice


class ImportError_(Exception):
    pass


def import_exam_from_dict(data: dict, created_by) -> Exam:
    required = ["subject", "title"]
    for field in required:
        if field not in data:
            raise ImportError_(f"Missing required field: {field}")

    exam = Exam.objects.create(
        subject=data["subject"],
        title=data["title"],
        seconds_per_question=data.get("secondsPerQuestion", 60),
        hints_enabled=data.get("hintsEnabled", True),
        created_by=created_by,
    )

    order = 1

    for item in data.get("multipleChoice", []):
        q = Question.objects.create(
            exam=exam,
            qtype="multipleChoice",
            text=item["text"],
            hint=item.get("hint", ""),
            order=order,
        )
        options = item.get("options", [])
        answer_index = item.get("answerIndex")
        for i, opt_text in enumerate(options):
            Choice.objects.create(
                question=q,
                text=opt_text,
                is_correct=(i == answer_index),
                order=i,
            )
        order += 1

    for item in data.get("boolean", []):
        q = Question.objects.create(
            exam=exam,
            qtype="boolean",
            text=item["text"],
            hint=item.get("hint", ""),
            order=order,
        )
        correct = bool(item.get("answer"))
        Choice.objects.create(question=q, text="True", is_correct=correct, order=0)
        Choice.objects.create(question=q, text="False", is_correct=(not correct), order=1)
        order += 1

    for item in data.get("identification", []):
        Question.objects.create(
            exam=exam,
            qtype="identification",
            text=item["text"],
            hint=item.get("hint", ""),
            order=order,
            identification_answer=item.get("answer", ""),
        )
        order += 1

    return exam


def import_exam_from_file(path: str, created_by) -> Exam:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return import_exam_from_dict(data, created_by)

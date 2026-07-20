"""Imports classroom JSON questionnaires into Exam/Question/Choice rows.

Supports TWO shapes:

1. Original grouped-array schema (see data/sampletopic.json):
{
  "subject": "...", "title": "...", "secondsPerQuestion": 60, "hintsEnabled": true,
  "multipleChoice": [{"id","text","options":[...], "answerIndex", "hint"}],
  "boolean": [{"id","text","answer": true/false, "hint"}],
  "identification": [{"id","text","answer", "hint"}]
}

2. Flat questions[] schema, adds image + module support + a Game Mode flag
   (see data/sia_exam_sample.json):
{
  "subject": "...", "title": "...", "secondsPerQuestion": 45, "hintsEnabled": true,
  "gameMode": false,
  "questions": [
    {"id","module","type": "multiple_choice"|"true_false"|"identification",
     "text","options":[...],"answerIndex","imageLink","hint"}
  ]
}
   For "multiple_choice" and "true_false", options+answerIndex are used
   directly (true_false questions just have 2 options). For
   "identification"/"short_answer" types, an "answer" string is expected
   instead of options/answerIndex.
"""
import json

from core.models import Exam, Question, Choice


class ImportError_(Exception):
    pass


def _create_choice_question(exam, order, text, hint, options, answer_index, image_url="", module=""):
    q = Question.objects.create(
        exam=exam, qtype="multipleChoice", text=text, hint=hint, order=order,
        image_url=image_url or "", module=module or "",
    )
    for i, opt_text in enumerate(options):
        Choice.objects.create(question=q, text=opt_text, is_correct=(i == answer_index), order=i)
    return q


def _import_flat_schema(exam, data):
    order = 1
    for item in data.get("questions", []):
        text = item.get("text", "")
        hint = item.get("hint", "")
        image_url = item.get("imageLink") or ""
        module = item.get("module") or ""
        qtype = (item.get("type") or "multiple_choice").lower()

        if qtype in ("identification", "short_answer", "identification_answer"):
            Question.objects.create(
                exam=exam, qtype="identification", text=text, hint=hint, order=order,
                image_url=image_url, module=module,
                identification_answer=item.get("answer", ""),
            )
        else:
            # "multiple_choice" and "true_false" both ship explicit
            # options + answerIndex in this schema, so they're handled
            # identically — a true_false question is just a 2-option MC.
            _create_choice_question(
                exam, order, text, hint,
                options=item.get("options", []),
                answer_index=item.get("answerIndex"),
                image_url=image_url, module=module,
            )
        order += 1


def _import_grouped_schema(exam, data):
    order = 1

    for item in data.get("multipleChoice", []):
        _create_choice_question(
            exam, order, item["text"], item.get("hint", ""),
            options=item.get("options", []), answer_index=item.get("answerIndex"),
            image_url=item.get("imageLink", ""),
        )
        order += 1

    for item in data.get("boolean", []):
        q = Question.objects.create(
            exam=exam, qtype="boolean", text=item["text"], hint=item.get("hint", ""),
            order=order, image_url=item.get("imageLink", ""),
        )
        correct = bool(item.get("answer"))
        Choice.objects.create(question=q, text="True", is_correct=correct, order=0)
        Choice.objects.create(question=q, text="False", is_correct=(not correct), order=1)
        order += 1

    for item in data.get("identification", []):
        Question.objects.create(
            exam=exam, qtype="identification", text=item["text"], hint=item.get("hint", ""),
            order=order, image_url=item.get("imageLink", ""),
            identification_answer=item.get("answer", ""),
        )
        order += 1


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
        game_mode=bool(data.get("gameMode", False)),
        created_by=created_by,
    )

    if "questions" in data:
        _import_flat_schema(exam, data)
    else:
        _import_grouped_schema(exam, data)

    return exam


def import_exam_from_file(path: str, created_by) -> Exam:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return import_exam_from_dict(data, created_by)

"""Grading helpers, including identification-answer normalization.

Normalization rules (see plan.md):
- case-insensitive
- leading/trailing whitespace ignored
- ALL internal whitespace ignored (so "PHP My Admin" == "phpmyadmin")
- genuine misspellings are NOT tolerated (no fuzzy matching)

Identification answers also accept the correct answer appearing as a whole
word/phrase within the submitted text (e.g. correct answer "WHERE" matches
submitted "WHERE clause"), so students aren't marked wrong for including
extra context around a correct term. Matching still requires word
boundaries, so "elsewhere" does not match "where".
"""

import re


def normalize_answer(text: str) -> str:
    if text is None:
        return ""
    return "".join(text.strip().lower().split())


def _collapse_whitespace(text: str) -> str:
    if text is None:
        return ""
    return " ".join(text.strip().lower().split())


def grade_identification(submitted_text: str, correct_answer: str) -> bool:
    if normalize_answer(submitted_text) == normalize_answer(correct_answer):
        return True

    correct = _collapse_whitespace(correct_answer)
    if not correct:
        return False
    submitted = _collapse_whitespace(submitted_text)
    return re.search(r"\b" + re.escape(correct) + r"\b", submitted) is not None


def grade_choice(selected_choice, question) -> bool:
    """selected_choice: a Choice instance (or None)."""
    if selected_choice is None:
        return False
    return bool(selected_choice.is_correct) and selected_choice.question_id == question.id


def grade_boolean(submitted_text: str, correct_bool: bool) -> bool:
    if submitted_text is None:
        return False
    val = submitted_text.strip().lower()
    submitted_bool = val in ("true", "1", "yes")
    return submitted_bool == correct_bool

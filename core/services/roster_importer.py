"""Bulk student-roster import for a single exam.

Supported formats:

1. Plain .txt — one student name per line:

    Doe, Jane
    Dela Cruz, Juan
    Alonso, Martin

   Passcodes are auto-generated (random 6-digit numeric) since none are
   given. To set explicit passcodes in a .txt file, add a pipe:

    Doe, Jane|482113
    Dela Cruz, Juan|990201

2. .json — either a plain list of names (passcodes auto-generated):

    ["Doe, Jane", "Dela Cruz, Juan", "Alonso, Martin"]

   or a list of objects with explicit passcodes:

    [{"name": "Doe, Jane", "passcode": "482113"}, ...]

Existing students (same name, same exam) are left alone unless the import
provides an explicit passcode for them, in which case it's updated.
"""
import json
import secrets

from core.models import Student


class RosterImportError(Exception):
    pass


def _generate_passcode(existing_codes, length=6):
    while True:
        code = "".join(secrets.choice("0123456789") for _ in range(length))
        if code not in existing_codes:
            return code


def parse_roster_txt(text: str):
    entries = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "|" in line:
            name, code = line.split("|", 1)
            name, code = name.strip(), code.strip()
        else:
            name, code = line, ""
        if name:
            entries.append((name, code or None))
    return entries


def parse_roster_json(raw_bytes: bytes):
    try:
        data = json.loads(raw_bytes)
    except json.JSONDecodeError as e:
        raise RosterImportError(f"Invalid JSON: {e}")
    if not isinstance(data, list):
        raise RosterImportError("JSON roster must be a list of names or {name, passcode} objects.")

    entries = []
    for item in data:
        if isinstance(item, str):
            name, code = item.strip(), None
        elif isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            code = item.get("passcode")
            code = str(code).strip() if code not in (None, "") else None
        else:
            continue
        if name:
            entries.append((name, code))
    return entries


def import_roster(exam, entries):
    """entries: list of (name, passcode_or_None). Returns a list of dicts
    describing what happened, for a confirmation screen."""
    existing_codes = set(Student.objects.filter(exam=exam).values_list("passcode", flat=True))
    results = []

    for name, code in entries:
        student, created = Student.objects.get_or_create(
            exam=exam, name=name, defaults={"passcode": ""},
        )
        status = "added" if created else "already existed"

        if code:
            student.passcode = code
            student.save(update_fields=["passcode"])
            existing_codes.add(code)
            if not created:
                status = "passcode updated"
        elif created or not student.passcode:
            new_code = _generate_passcode(existing_codes)
            student.passcode = new_code
            student.save(update_fields=["passcode"])
            existing_codes.add(new_code)

        results.append({"name": student.name, "passcode": student.passcode, "status": status})

    return results

import json
import random
from datetime import timedelta

from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse, HttpResponseBadRequest
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Exam, Question, Submission, Answer, Choice, Student, Violation
from .utils.grading import grade_identification

# --- Tab-switch policy -----------------------------------------------------
# Attempts 1-6: warning only. 7th/8th/9th: escalating timed locks.
# 10th: exam is force-closed (submitted as-is), regardless of whether the
# student had finished all questions or not.
TAB_LOCK_SCHEDULE = {7: 10, 8: 20, 9: 30}  # attempt_number -> lock seconds
TAB_CLOSE_AT = 10

# Answers submitted faster than this are flagged in the CSV export as
# "suspiciously fast" (informational only — doesn't block or penalize).
SUSPICIOUSLY_FAST_SECONDS = 3.0


def _get_submission(request):
    sub_id = request.session.get("submission_id")
    if not sub_id:
        return None
    return Submission.objects.filter(id=sub_id).first()


def _ensure_answers(submission):
    """Idempotently create one Answer row per question for this submission."""
    existing_qids = set(submission.answers.values_list("question_id", flat=True))
    to_create = [
        Answer(submission=submission, question=q)
        for q in submission.exam.questions.all()
        if q.id not in existing_qids
    ]
    if to_create:
        Answer.objects.bulk_create(to_create)


def _get_ordered_questions(submission):
    """Returns this student's questions in THEIR randomized order (see
    Submission.question_order). Falls back to the exam's natural order if
    question_order is empty (e.g. rows created before this feature existed).
    """
    id_order = submission.question_order
    if not id_order:
        return list(submission.exam.questions.order_by("order"))
    by_id = {q.id: q for q in submission.exam.questions.filter(id__in=id_order)}
    return [by_id[qid] for qid in id_order if qid in by_id]


def _score_summary(submission):
    answers = submission.answers.all()
    total = submission.exam.questions.count()
    correct = answers.filter(is_correct=True).count()
    answered = answers.filter(answered=True).count()
    percentage = round((correct / total) * 100, 1) if total else 0
    return {"score": correct, "total_questions": total, "answered": answered, "percentage": percentage}


def _done_context(submission, no_questions=False):
    ctx = {"submission": submission, "done": True, "no_questions": no_questions}
    if not no_questions:
        ctx.update(_score_summary(submission))
    return ctx


# --- Login -----------------------------------------------------------------

def login_view(request):
    active_exams = Exam.objects.filter(is_active=True, is_archived=False).prefetch_related("students")

    if request.method == "POST":
        exam_id = request.POST.get("exam_id")
        student_id = request.POST.get("student_id")
        passcode = request.POST.get("passcode", "").strip()
        exam = get_object_or_404(Exam, id=exam_id, is_active=True)

        student = Student.objects.filter(id=student_id, exam=exam).first()
        if not student or student.passcode != passcode:
            return render(request, "login.html", {
                "exams": active_exams,
                "error": "Incorrect name or passcode. Check with your teacher if you're not sure.",
                "selected_exam_id": exam_id,
            })

        if not exam.questions.exists():
            return render(request, "login.html", {
                "exams": active_exams,
                "error": f'"{exam.title}" has no questions yet. Ask your teacher to add '
                         f"questions (or import a JSON questionnaire) before starting.",
                "selected_exam_id": exam_id,
            })

        student_name = student.name

        submission, created = Submission.objects.get_or_create(
            student_name=student_name, exam=exam,
        )
        if submission.closed:
            return render(request, "login.html", {
                "exams": active_exams,
                "error": "This exam has already been submitted/closed for that name.",
            })

        if created:
            # Randomize question order per-student so exact "question N" answers
            # are harder to share between students taking the same exam.
            qids = list(exam.questions.values_list("id", flat=True))
            random.shuffle(qids)
            submission.question_order = qids

        _ensure_answers(submission)

        if created or submission.current_question is None:
            submission.current_question = 1

        if created:
            questions = _get_ordered_questions(submission)
            if questions:
                Answer.objects.filter(submission=submission, question=questions[0]).update(
                    question_started_at=timezone.now()
                )
        submission.last_heartbeat = timezone.now()
        submission.save()

        request.session["submission_id"] = submission.id
        return redirect("exam")

    return render(request, "login.html", {"exams": active_exams})


# --- Question phase ----------------------------------------------------------

def exam_view(request):
    submission = _get_submission(request)
    if not submission:
        return redirect("login")

    if submission.closed or submission.phase == "done":
        return render(request, "exam.html", _done_context(submission))

    if submission.phase == "review":
        return redirect("review")

    if submission.lock_until and submission.lock_until > timezone.now():
        return redirect("locked")

    exam = submission.exam
    questions = _get_ordered_questions(submission)
    total = len(questions)

    if total == 0:
        submission.phase = "done"
        submission.closed = True
        submission.save()
        return render(request, "exam.html", _done_context(submission, no_questions=True))

    _advance_past_expired_questions(submission, questions)

    if submission.phase == "review":
        return redirect("review")
    if submission.closed:
        return render(request, "exam.html", _done_context(submission))

    current_q = questions[submission.current_question - 1]
    answer = Answer.objects.get(submission=submission, question=current_q)
    if not answer.question_started_at:
        answer.question_started_at = timezone.now()
        answer.save()

    elapsed = (timezone.now() - answer.question_started_at).total_seconds()
    remaining = max(0, exam.seconds_per_question - elapsed)

    return render(request, "exam.html", {
        "submission": submission,
        "question": current_q,
        "choices": current_q.choices.all() if current_q.qtype != "identification" else None,
        "remaining_seconds": int(remaining),
        "total_seconds": exam.seconds_per_question,
        "q_number": submission.current_question,
        "q_total": total,
        "hints_enabled": exam.hints_enabled,
        "done": False,
    })


def _advance_past_expired_questions(submission, questions):
    """Auto-skip any question(s) whose server-computed time has run out.
    Handles the case where a student was disconnected across multiple
    question windows -- catches the session up to the current true state.
    """
    exam = submission.exam
    total = len(questions)
    while submission.current_question <= total:
        q = questions[submission.current_question - 1]
        answer, _ = Answer.objects.get_or_create(submission=submission, question=q)
        if not answer.question_started_at:
            answer.question_started_at = timezone.now()
            answer.save()
            return
        elapsed = (timezone.now() - answer.question_started_at).total_seconds()
        remaining = max(0, exam.seconds_per_question - elapsed)
        if remaining > 0:
            return
        if not answer.answered:
            answer.skipped = True
            answer.time_spent_seconds = exam.seconds_per_question
            answer.save()
        _move_to_next_question(submission, questions)


def _move_to_next_question(submission, questions):
    total = len(questions)
    if submission.current_question >= total:
        submission.phase = "review"
        submission.save()
        return
    submission.current_question += 1
    next_q = questions[submission.current_question - 1]
    Answer.objects.filter(submission=submission, question=next_q).update(
        question_started_at=timezone.now()
    )
    submission.save()


@require_POST
def submit_answer(request):
    submission = _get_submission(request)
    if not submission:
        return redirect("login")
    if submission.phase != "question" or submission.closed:
        return redirect("exam")
    if submission.lock_until and submission.lock_until > timezone.now():
        return redirect("locked")

    exam = submission.exam
    questions = _get_ordered_questions(submission)
    current_q = questions[submission.current_question - 1]
    answer = Answer.objects.get(submission=submission, question=current_q)

    elapsed = (timezone.now() - answer.question_started_at).total_seconds()
    remaining = max(0, exam.seconds_per_question - elapsed)

    action = request.POST.get("action")

    if remaining <= 0:
        # Time already ran out server-side; treat as auto-skip regardless of
        # what the client thought it was submitting.
        answer.skipped = True
        answer.time_spent_seconds = exam.seconds_per_question
        answer.save()
    elif action == "skip":
        answer.skipped = True
        answer.time_spent_seconds = elapsed
        answer.save()
        submission.review_bank_seconds += int(remaining)
    elif action == "submit":
        submitted_text = request.POST.get("answer_text", "")
        answer.answer_text = submitted_text
        answer.answered = True
        answer.skipped = False
        answer.is_correct = _grade(current_q, submitted_text)
        answer.time_spent_seconds = elapsed
        answer.save()
        submission.review_bank_seconds += int(remaining)
    else:
        return HttpResponseBadRequest("Unknown action")

    submission.save()
    _move_to_next_question(submission, questions)
    return redirect("exam")


def _grade(question, submitted_text):
    if question.qtype == "identification":
        return grade_identification(submitted_text, question.identification_answer)
    if question.qtype in ("multipleChoice", "boolean"):
        # Both render as a set of Choice buttons; submitted_text is a Choice id.
        try:
            choice = Choice.objects.get(id=submitted_text, question=question)
            return choice.is_correct
        except (Choice.DoesNotExist, ValueError):
            return False
    return False


# --- Review phase --------------------------------------------------------

def _pending_review_answers(submission):
    """Skipped/unanswered Answers, ordered to match this student's
    randomized question order (falls back to natural order)."""
    pending = list(submission.answers.filter(skipped=True, answered=False).select_related("question"))
    order = submission.question_order or []
    if order:
        index = {qid: i for i, qid in enumerate(order)}
        pending.sort(key=lambda a: index.get(a.question_id, 10**9))
    else:
        pending.sort(key=lambda a: a.question.order)
    return pending


def review_view(request):
    submission = _get_submission(request)
    if not submission:
        return redirect("login")
    if submission.closed or submission.phase == "done":
        return render(request, "exam.html", _done_context(submission))
    if submission.phase == "question":
        return redirect("exam")

    pending = _pending_review_answers(submission)

    if not pending or submission.review_bank_seconds <= 0:
        submission.phase = "done"
        submission.closed = True
        submission.save()
        return render(request, "exam.html", _done_context(submission))

    answer = pending[0]
    if not answer.question_started_at:
        answer.question_started_at = timezone.now()
        answer.save()

    elapsed = (timezone.now() - answer.question_started_at).total_seconds()
    remaining = max(0, submission.review_bank_seconds - elapsed)

    if remaining <= 0:
        submission.review_bank_seconds = 0
        submission.phase = "done"
        submission.closed = True
        submission.save()
        return render(request, "exam.html", _done_context(submission))

    question = answer.question
    return render(request, "review.html", {
        "submission": submission,
        "question": question,
        "choices": question.choices.all() if question.qtype != "identification" else None,
        "remaining_seconds": int(remaining),
        "bank_seconds": submission.review_bank_seconds,
        "remaining_count": len(pending),
        "hints_enabled": submission.exam.hints_enabled,
        "done": False,
    })


@require_POST
def submit_review_answer(request):
    submission = _get_submission(request)
    if not submission or submission.phase != "review" or submission.closed:
        return redirect("review")

    pending = _pending_review_answers(submission)
    if not pending:
        submission.phase = "done"
        submission.closed = True
        submission.save()
        return redirect("review")

    answer = pending[0]
    elapsed = (timezone.now() - answer.question_started_at).total_seconds()
    spent = min(elapsed, submission.review_bank_seconds)

    action = request.POST.get("action")
    if action == "submit":
        submitted_text = request.POST.get("answer_text", "")
        answer.answer_text = submitted_text
        answer.answered = True
        answer.is_correct = _grade(answer.question, submitted_text)
        answer.time_spent_seconds = spent
    # "skip" (leave for later / out of time): stays skipped, unanswered.
    answer.question_started_at = None
    answer.save()

    submission.review_bank_seconds = max(0, int(submission.review_bank_seconds - spent))
    submission.save()
    return redirect("review")


@require_POST
def finalize_submission(request):
    """Explicit final-submit button in the review phase."""
    submission = _get_submission(request)
    if submission and not submission.closed:
        submission.phase = "done"
        submission.closed = True
        submission.save()
    return redirect("exam")


# --- Anti-cheat: tab switching & disconnection lock -----------------------

def _extract_violation_payload(request, type_maxlen=50):
    """Shared by tab_violation and report_violation: accepts either a JSON
    body ({"type": "...", "details": {...}}) or a form POST (type=...)."""
    vtype, details = "unknown", ""
    if request.content_type == "application/json":
        try:
            payload = json.loads(request.body or b"{}")
        except json.JSONDecodeError:
            payload = {}
        vtype = (payload.get("type") or "unknown")[:type_maxlen]
        extra = {k: v for k, v in payload.items() if k != "type"}
        details = json.dumps(extra)[:2000] if extra else ""
    else:
        vtype = (request.POST.get("type") or "unknown")[:type_maxlen]
        details = (request.POST.get("details") or "")[:2000]
    return vtype, details


@require_POST
def tab_violation(request):
    """Escalating violations: tab-switch / window-blur. Drives the
    warn -> lock -> auto-close schedule."""
    submission = _get_submission(request)
    if not submission:
        return JsonResponse({"error": "no session"}, status=400)
    if submission.closed:
        return JsonResponse({"attempts": submission.tab_attempts, "action": "closed",
                              "locked": False, "closed": True, "max": TAB_CLOSE_AT})

    violation_type, details = _extract_violation_payload(request)
    Violation.objects.create(submission=submission, violation_type=violation_type, details=details)

    submission.tab_attempts += 1
    submission.last_violation_type = violation_type
    n = submission.tab_attempts

    if n >= TAB_CLOSE_AT:
        submission.closed = True
        submission.phase = "done"
        submission.save()
        return JsonResponse({"attempts": n, "action": "closed",
                              "locked": False, "closed": True, "max": TAB_CLOSE_AT})

    if n in TAB_LOCK_SCHEDULE:
        seconds = TAB_LOCK_SCHEDULE[n]
        submission.lock_until = timezone.now() + timedelta(seconds=seconds)
        submission.save()
        return JsonResponse({"attempts": n, "action": "locked", "lock_seconds": seconds,
                              "locked": True, "closed": False, "max": TAB_CLOSE_AT})

    submission.save()
    return JsonResponse({"attempts": n, "action": "warning",
                          "locked": False, "closed": False, "max": TAB_CLOSE_AT})


@require_POST
def report_violation(request):
    """Log-only violations: copy_attempt, paste_attempt, prolonged_idle,
    fingerprint_mismatch, etc. Recorded for teacher visibility but does NOT
    affect the tab-switch lock/close schedule above — that stays scoped
    strictly to tab-switch/window-blur, per the exam's anti-cheat design."""
    submission = _get_submission(request)
    if not submission:
        return JsonResponse({"error": "no session"}, status=400)
    if submission.closed:
        return JsonResponse({"status": "ignored", "reason": "submission already closed"})

    violation_type, details = _extract_violation_payload(request)
    Violation.objects.create(submission=submission, violation_type=violation_type, details=details)
    return JsonResponse({"status": "logged"})


def locked_view(request):
    submission = _get_submission(request)
    if not submission:
        return redirect("login")
    if not submission.lock_until or submission.lock_until <= timezone.now():
        return redirect("exam")
    remaining = int((submission.lock_until - timezone.now()).total_seconds())
    return render(request, "locked.html", {"remaining_seconds": max(0, remaining)})


# --- Status/heartbeat API (polled by exam.js) -----------------------------

def status_api(request):
    submission = _get_submission(request)
    if not submission:
        return JsonResponse({"error": "no session"}, status=400)

    submission.last_heartbeat = timezone.now()
    submission.save(update_fields=["last_heartbeat"])

    locked = bool(submission.lock_until and submission.lock_until > timezone.now())
    lock_remaining = (int((submission.lock_until - timezone.now()).total_seconds())
                       if locked else 0)

    payload = {
        "phase": submission.phase,
        "closed": submission.closed,
        "locked": locked,
        "lock_remaining_seconds": lock_remaining,
        "tab_attempts": submission.tab_attempts,
    }

    if submission.phase == "question" and not submission.closed and not locked:
        exam = submission.exam
        questions = _get_ordered_questions(submission)
        if submission.current_question <= len(questions):
            q = questions[submission.current_question - 1]
            answer = Answer.objects.filter(submission=submission, question=q).first()
            if answer and answer.question_started_at:
                elapsed = (timezone.now() - answer.question_started_at).total_seconds()
                payload["remaining_seconds"] = max(0, int(exam.seconds_per_question - elapsed))
                payload["expired"] = elapsed >= exam.seconds_per_question
    elif submission.phase == "review" and not submission.closed:
        pending = _pending_review_answers(submission)
        answer = pending[0] if pending else None
        if answer and answer.question_started_at:
            elapsed = (timezone.now() - answer.question_started_at).total_seconds()
            payload["remaining_seconds"] = max(0, int(submission.review_bank_seconds - elapsed))
            payload["expired"] = elapsed >= submission.review_bank_seconds

    return JsonResponse(payload)


# --- Teacher live monitoring -----------------------------------------------

@staff_member_required
def teacher_monitor(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id)
    return render(request, "teacher_monitor.html", {"exam": exam})


@staff_member_required
def teacher_monitor_data(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id)
    now = timezone.now()
    total_questions = exam.questions.count()
    rows = []
    for sub in exam.submissions.all().order_by("student_name"):
        stale = True
        seconds_ago = None
        if sub.last_heartbeat:
            seconds_ago = (now - sub.last_heartbeat).total_seconds()
            stale = seconds_ago > 15

        violation_qs = sub.violations.all()
        recent_violation = violation_qs.filter(
            created_at__gte=now - timedelta(seconds=30)
        ).exists()

        answers = sub.answers.all()
        answered = answers.filter(answered=True).count()
        correct = answers.filter(is_correct=True).count()

        rows.append({
            "student_name": sub.student_name,
            "current_question": sub.current_question,
            "phase": sub.phase,
            "tab_attempts": sub.tab_attempts,
            "violation_count": violation_qs.count(),
            "recent_violation": recent_violation,
            "answered": answered,
            "correct": correct,
            "total_questions": total_questions,
            "closed": sub.closed,
            "stale": stale,
            "seconds_ago": int(seconds_ago) if seconds_ago is not None else None,
        })

    # Rank live: most correct first, ties broken by most answered so far.
    rows.sort(key=lambda r: (-r["correct"], -r["answered"], r["student_name"]))
    return JsonResponse({"exam": exam.title, "total_questions": total_questions, "students": rows})

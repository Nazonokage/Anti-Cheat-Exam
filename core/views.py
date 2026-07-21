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

# Game Mode: how many defense charges the "defense" buff choice grants.
DEFENSE_BUFF_AMOUNT = 3


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


def _game_context(submission):
    """Buff state for game-mode exams, plus a POP of any pending one-shot
    checkpoint notice (shown once, then cleared)."""
    if not submission.exam.game_mode:
        return {}
    ctx = {
        "game_mode": True,
        "attack_charges": submission.attack_charges,
        "defense_charges": submission.defense_charges,
        "time_boost_charges": submission.time_boost_charges,
        "pending_buff_choice": submission.pending_buff_choice,
    }
    if submission.pending_checkpoint_notice:
        ctx["checkpoint_notice"] = json.dumps(submission.pending_checkpoint_notice)
        submission.pending_checkpoint_notice = None
        submission.save(update_fields=["pending_checkpoint_notice"])
    return ctx


def _missed_questions(submission):
    """Questions the student got wrong OR never answered, in their own
    question order, with human-readable answer text (not raw Choice ids)."""
    order = submission.question_order or list(
        submission.exam.questions.order_by("order").values_list("id", flat=True)
    )
    answers_by_qid = {a.question_id: a for a in submission.answers.select_related("question")}
    missed = []
    for qid in order:
        a = answers_by_qid.get(qid)
        if not a or a.is_correct:
            continue
        q = a.question
        if q.qtype == "identification":
            your_answer = a.answer_text or "(no answer)"
            correct_answer = q.identification_answer
        else:
            correct_choice = q.choices.filter(is_correct=True).first()
            correct_answer = correct_choice.text if correct_choice else ""
            your_choice = None
            if a.answer_text:
                try:
                    your_choice = q.choices.filter(id=int(a.answer_text)).first()
                except (ValueError, TypeError):
                    your_choice = None
            your_answer = your_choice.text if your_choice else "(no answer)"
        missed.append({
            "question_text": q.text,
            "your_answer": your_answer,
            "correct_answer": correct_answer,
            "was_answered": a.answered,
        })
    return missed


def _done_context(submission, no_questions=False):
    ctx = {"submission": submission, "done": True, "no_questions": no_questions}
    if not no_questions:
        ctx.update(_score_summary(submission))
        ctx.update(_game_context(submission))
        ctx["missed_questions"] = _missed_questions(submission)
        ctx["missed_questions_json"] = json.dumps(ctx["missed_questions"])
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
        **_game_context(submission),
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


def _effective_score(submission):
    """Game Mode leaderboard score = real correct-answer count minus any
    attack penalties. Never touches Answer.is_correct or the real grade."""
    correct = submission.answers.filter(is_correct=True).count()
    return correct - submission.score_penalty


def _compute_rank(submission):
    exam = submission.exam
    scored = [(s.id, _effective_score(s)) for s in exam.submissions.all()]
    scored.sort(key=lambda t: -t[1])
    rank = next((i for i, (sid, _) in enumerate(scored, start=1) if sid == submission.id), len(scored))
    return rank, len(scored)


def _decay_defense_on_question_finish(submission):
    """Defense decays by 1 every time a question is finished (answered,
    skipped, or auto-skipped) — independent of the buff-choice milestone
    below, and independent of whether any attack was blocked."""
    if not submission.exam.game_mode:
        return
    submission.defense_charges = max(0, submission.defense_charges - 1)


def _check_buff_milestone(submission):
    """Skill-based buff trigger: every 5th CORRECT answer (not every 5th
    question attempted) queues a buff CHOICE for the student to make —
    see game_choose_buff. Call this right after grading an answer correct."""
    if not submission.exam.game_mode:
        return
    correct_count = submission.answers.filter(is_correct=True).count()
    milestone = (correct_count // 5) * 5
    if milestone > 0 and milestone > submission.last_buff_milestone:
        submission.last_buff_milestone = milestone
        submission.pending_buff_choice = True
        rank, total = _compute_rank(submission)
        submission.pending_checkpoint_notice = {
            "correct_count": correct_count,
            "rank": rank,
            "total": total,
        }


def _move_to_next_question(submission, questions):
    total = len(questions)
    _decay_defense_on_question_finish(submission)

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
        if answer.is_correct:
            _check_buff_milestone(submission)
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
        **_game_context(submission),
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

    if action == "submit" and answer.is_correct:
        _check_buff_milestone(submission)

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


# --- Game Mode: student-facing leaderboard + buffs -------------------------

def game_leaderboard(request):
    """Live-ranked leaderboard for the 'realtime ranking tab' students see
    during the exam. Uses the game score (correct - score_penalty), NOT the
    real grade — attacks never touch actual grading."""
    submission = _get_submission(request)
    if not submission:
        return JsonResponse({"error": "no session"}, status=400)
    exam = submission.exam
    if not exam.game_mode:
        return JsonResponse({"error": "game mode not enabled for this exam"}, status=400)

    rows = [
        {"id": s.id, "name": s.student_name, "score": _effective_score(s), "is_you": s.id == submission.id}
        for s in exam.submissions.all()
    ]
    rows.sort(key=lambda r: -r["score"])
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    return JsonResponse({"leaderboard": rows})


def game_opponents(request):
    """Attackable targets: other still-active students in the same exam."""
    submission = _get_submission(request)
    if not submission:
        return JsonResponse({"error": "no session"}, status=400)
    exam = submission.exam
    if not exam.game_mode:
        return JsonResponse({"error": "game mode not enabled for this exam"}, status=400)

    opponents = exam.submissions.exclude(id=submission.id).filter(closed=False)
    return JsonResponse({"opponents": [{"id": s.id, "name": s.student_name} for s in opponents]})


@require_POST
def game_attack(request):
    """Spend 1 attack charge to hit another player: -1 to their game score,
    UNLESS they have a defense charge, in which case it's blocked and
    consumes one of THEIR defense charges instead. Never touches real
    grading (Answer.is_correct) — this only affects score_penalty, the
    Game Mode leaderboard number."""
    submission = _get_submission(request)
    if not submission or submission.closed:
        return JsonResponse({"error": "no session"}, status=400)
    exam = submission.exam
    if not exam.game_mode:
        return JsonResponse({"error": "game mode not enabled for this exam"}, status=400)
    if submission.attack_charges <= 0:
        return JsonResponse({"error": "no attack charges available"}, status=400)

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        payload = {}
    target = Submission.objects.filter(
        id=payload.get("target_id"), exam=exam, closed=False,
    ).exclude(id=submission.id).first()
    if not target:
        return JsonResponse({"error": "invalid or unavailable target"}, status=400)

    submission.attack_charges -= 1
    submission.save(update_fields=["attack_charges"])

    blocked = target.defense_charges > 0
    if blocked:
        target.defense_charges -= 1
        target.save(update_fields=["defense_charges"])
    else:
        target.score_penalty += 1
        target.save(update_fields=["score_penalty"])

    return JsonResponse({
        "status": "ok", "blocked": blocked, "target_name": target.student_name,
        "attack_charges_left": submission.attack_charges,
    })


@require_POST
def game_time_boost(request):
    """Spend 1 time-boost charge to add 30s to the CURRENT question's
    remaining time (question phase only — server-authoritative, same
    mechanism as the normal timer: shifts question_started_at back)."""
    submission = _get_submission(request)
    if not submission or submission.closed:
        return JsonResponse({"error": "no session"}, status=400)
    exam = submission.exam
    if not exam.game_mode:
        return JsonResponse({"error": "game mode not enabled for this exam"}, status=400)
    if submission.time_boost_charges <= 0:
        return JsonResponse({"error": "no time-boost charges available"}, status=400)
    if submission.phase != "question":
        return JsonResponse({"error": "time boost is only usable during the question phase"}, status=400)

    questions = _get_ordered_questions(submission)
    if submission.current_question > len(questions):
        return JsonResponse({"error": "no active question"}, status=400)
    current_q = questions[submission.current_question - 1]
    answer = Answer.objects.filter(submission=submission, question=current_q).first()
    if answer and answer.question_started_at:
        # To INCREASE remaining time, move question_started_at FORWARD
        # (closer to "now"), which reduces elapsed = now - question_started_at.
        answer.question_started_at += timedelta(seconds=30)
        answer.save(update_fields=["question_started_at"])

    submission.time_boost_charges -= 1
    submission.save(update_fields=["time_boost_charges"])
    return JsonResponse({"status": "ok", "time_boost_charges_left": submission.time_boost_charges})


@require_POST
def game_choose_buff(request):
    """Spend a pending buff-choice (earned every 5 correct answers) on
    exactly ONE of attack / defense / time_boost — not all three."""
    submission = _get_submission(request)
    if not submission or submission.closed:
        return JsonResponse({"error": "no session"}, status=400)
    if not submission.exam.game_mode:
        return JsonResponse({"error": "game mode not enabled for this exam"}, status=400)
    if not submission.pending_buff_choice:
        return JsonResponse({"error": "no buff choice available right now"}, status=400)

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        payload = {}
    choice = payload.get("buff")

    if choice == "attack":
        submission.attack_charges += 1
    elif choice == "defense":
        submission.defense_charges = DEFENSE_BUFF_AMOUNT
    elif choice == "time_boost":
        submission.time_boost_charges += 1
    else:
        return JsonResponse({"error": "invalid buff choice"}, status=400)

    submission.pending_buff_choice = False
    submission.save()
    return JsonResponse({
        "status": "ok", "chosen": choice,
        "attack_charges": submission.attack_charges,
        "defense_charges": submission.defense_charges,
        "time_boost_charges": submission.time_boost_charges,
    })

from django.db import models
from django.contrib.auth.models import User


class Exam(models.Model):
    subject = models.CharField(max_length=100)
    title = models.CharField(max_length=200)
    seconds_per_question = models.IntegerField(default=60)
    hints_enabled = models.BooleanField(default=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    is_active = models.BooleanField(default=False)
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} ({self.subject})"


class Question(models.Model):
    QTYPE_CHOICES = [
        ("multipleChoice", "Multiple Choice"),
        ("boolean", "True / False"),
        ("identification", "Identification"),
    ]

    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="questions")
    qtype = models.CharField(max_length=20, choices=QTYPE_CHOICES)
    text = models.TextField()
    hint = models.TextField(blank=True)
    order = models.IntegerField()
    # For identification questions, the correct answer is stored directly
    # (avoids needing a Choice row for a free-text question).
    identification_answer = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"Q{self.order}: {self.text[:50]}"


class Choice(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="choices")
    text = models.CharField(max_length=255)
    is_correct = models.BooleanField(default=False)
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return self.text


class Student(models.Model):
    """A teacher-issued roster entry: this is how a student authenticates,
    instead of free-typing their name. Passcodes are set manually by the
    teacher in Django Admin, per exam."""
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="students")
    name = models.CharField(max_length=100)
    passcode = models.CharField(max_length=50)

    class Meta:
        unique_together = ("exam", "name")
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.exam.title})"


class Submission(models.Model):
    PHASE_CHOICES = [
        ("question", "Question Phase"),
        ("review", "Review Phase"),
        ("done", "Completed"),
    ]

    student_name = models.CharField(max_length=100)
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="submissions")
    current_question = models.IntegerField(default=1)
    review_bank_seconds = models.IntegerField(default=0)
    phase = models.CharField(max_length=20, choices=PHASE_CHOICES, default="question")
    tab_attempts = models.IntegerField(default=0)
    last_violation_type = models.CharField(max_length=50, blank=True)
    # Randomized question order for THIS student (list of Question ids).
    # Populated once at first login so each student sees questions in a
    # different sequence, making it harder to share "question N is X".
    question_order = models.JSONField(default=list, blank=True)
    lock_until = models.DateTimeField(null=True, blank=True)
    last_heartbeat = models.DateTimeField(null=True, blank=True)
    closed = models.BooleanField(default=False)
    started_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("student_name", "exam")

    def __str__(self):
        return f"{self.student_name} - {self.exam.title}"


class Violation(models.Model):
    """One row per detected anti-cheat event. Two flavors share this table:
    - Escalating (tab-switch, window-blur): drive Submission.tab_attempts
      and the lock/close schedule (see views.py::tab_violation).
    - Log-only (copy_attempt, paste_attempt, prolonged_idle, ...): recorded
      for teacher visibility but do NOT affect the lock/close schedule
      (see views.py::report_violation).
    """
    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name="violations")
    violation_type = models.CharField(max_length=50)  # e.g. "tab-switch", "copy_attempt"
    details = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.submission.student_name} - {self.violation_type} @ {self.created_at:%H:%M:%S}"


class Answer(models.Model):
    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(Question, on_delete=models.CASCADE)
    answer_text = models.TextField(blank=True)
    skipped = models.BooleanField(default=False)
    answered = models.BooleanField(default=False)
    is_correct = models.BooleanField(default=False)
    question_started_at = models.DateTimeField(null=True, blank=True)
    time_spent_seconds = models.FloatField(null=True, blank=True)

    class Meta:
        unique_together = ("submission", "question")

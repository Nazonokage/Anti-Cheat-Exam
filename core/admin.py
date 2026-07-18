import csv
import json
from collections import Counter

from django.contrib import admin
from django.http import HttpResponse
from django import forms
from django.urls import path
from django.shortcuts import render, redirect
from django.contrib import messages
from django.utils import timezone

from .models import Exam, Question, Choice, Submission, Answer, Student, Violation
from .services.importer import import_exam_from_dict, ImportError_
from .views import SUSPICIOUSLY_FAST_SECONDS


class ChoiceInline(admin.TabularInline):
    model = Choice
    extra = 0


class QuestionInline(admin.TabularInline):
    model = Question
    extra = 0
    fields = ("order", "qtype", "text", "identification_answer")
    show_change_link = True


class JSONImportForm(forms.Form):
    json_file = forms.FileField(label="Exam JSON file")


class StudentInline(admin.TabularInline):
    model = Student
    extra = 3
    fields = ("name", "passcode")


@admin.register(Exam)
class ExamAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "subject", "is_active", "is_archived", "seconds_per_question",
                     "hints_enabled", "created_by", "question_count", "student_count", "created_at")
    list_filter = ("is_active", "is_archived", "subject")
    inlines = [QuestionInline, StudentInline]
    readonly_fields = ("id",)
    fields = ("id", "subject", "title", "seconds_per_question", "hints_enabled",
              "created_by", "is_active", "is_archived")
    actions = ["activate_exams", "deactivate_exams", "archive_exams", "export_results_csv"]
    change_list_template = "admin/core/exam/change_list.html"

    def question_count(self, obj):
        return obj.questions.count()
    question_count.short_description = "Questions"

    def student_count(self, obj):
        return obj.students.count()
    student_count.short_description = "Roster size"

    def activate_exams(self, request, queryset):
        queryset.update(is_active=True)
    activate_exams.short_description = "Activate selected exams"

    def deactivate_exams(self, request, queryset):
        queryset.update(is_active=False)
    deactivate_exams.short_description = "Deactivate selected exams"

    def archive_exams(self, request, queryset):
        queryset.update(is_active=False, is_archived=True)
    archive_exams.short_description = "Archive selected exams (deactivates too)"

    def export_results_csv(self, request, queryset):
        timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="exam_results_{timestamp}.csv"'
        writer = csv.writer(response)
        writer.writerow(["Exam", "Student", "Phase", "Tab Attempts", "Closed",
                          "Questions Answered", "Questions Correct", "Total Questions",
                          "Score %", "Total Violations", "Most Common Violation Type",
                          "Suspiciously Fast Answers (<3s)"])
        for exam in queryset:
            for sub in exam.submissions.all():
                answers = sub.answers.all()
                answered = answers.filter(answered=True).count()
                correct = answers.filter(is_correct=True).count()
                total_q = exam.questions.count()
                percentage = round((correct / total_q) * 100, 1) if total_q else 0

                violation_types = list(sub.violations.values_list("violation_type", flat=True))
                total_violations = len(violation_types)
                most_common = Counter(violation_types).most_common(1)
                most_common_type = most_common[0][0] if most_common else ""

                fast_answers = answers.filter(
                    answered=True, time_spent_seconds__isnull=False,
                    time_spent_seconds__lt=SUSPICIOUSLY_FAST_SECONDS,
                ).count()

                writer.writerow([
                    exam.title, sub.student_name, sub.phase, sub.tab_attempts,
                    sub.closed, answered, correct, total_q, percentage,
                    total_violations, most_common_type, fast_answers,
                ])
        return response
    export_results_csv.short_description = "Export results to CSV"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path("import-json/", self.admin_site.admin_view(self.import_json_view),
                 name="core_exam_import_json"),
        ]
        return custom + urls

    def import_json_view(self, request):
        if request.method == "POST":
            form = JSONImportForm(request.POST, request.FILES)
            if form.is_valid():
                try:
                    data = json.load(request.FILES["json_file"])
                    exam = import_exam_from_dict(data, request.user)
                    messages.success(
                        request,
                        f"Imported '{exam.title}' with {exam.questions.count()} questions. "
                        f"It is inactive — activate it below before students take it.",
                    )
                    return redirect("admin:core_exam_changelist")
                except (ImportError_, json.JSONDecodeError, KeyError) as e:
                    messages.error(request, f"Import failed: {e}")
        else:
            form = JSONImportForm()
        return render(request, "admin/core/exam/import_json.html", {"form": form})


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "exam", "passcode")
    list_filter = ("exam",)
    search_fields = ("name",)


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("order", "exam", "qtype", "text")
    list_filter = ("exam", "qtype")
    inlines = [ChoiceInline]


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ("id", "student_name", "exam", "phase", "current_question",
                     "tab_attempts", "last_violation_type", "closed", "last_heartbeat")
    list_filter = ("exam", "phase", "closed")


@admin.register(Violation)
class ViolationAdmin(admin.ModelAdmin):
    list_display = ("id", "submission", "violation_type", "created_at")
    list_filter = ("violation_type", "submission__exam")
    readonly_fields = ("submission", "violation_type", "created_at")

    def has_add_permission(self, request):
        return False


@admin.register(Answer)
class AnswerAdmin(admin.ModelAdmin):
    list_display = ("submission", "question", "answered", "skipped", "is_correct")
    list_filter = ("answered", "skipped", "is_correct")

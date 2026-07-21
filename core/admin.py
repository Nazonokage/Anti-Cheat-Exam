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
from .services.roster_importer import (
    parse_roster_txt, parse_roster_md, parse_roster_json, import_roster, RosterImportError,
)
from .views import SUSPICIOUSLY_FAST_SECONDS


def _parse_roster_upload(uploaded_file):
    """Dispatches to the right parser based on file extension. Returns a
    list of (name, passcode_or_None) entries."""
    raw = uploaded_file.read()
    filename = uploaded_file.name.lower()
    if filename.endswith(".json"):
        return parse_roster_json(raw)
    if filename.endswith(".md") or filename.endswith(".markdown"):
        return parse_roster_md(raw.decode("utf-8-sig"))
    return parse_roster_txt(raw.decode("utf-8-sig"))


class ChoiceInline(admin.TabularInline):
    model = Choice
    extra = 0


class QuestionInline(admin.TabularInline):
    model = Question
    extra = 0
    fields = ("order", "module", "qtype", "text", "image_url", "identification_answer")
    show_change_link = True


class JSONImportForm(forms.Form):
    json_file = forms.FileField(label="Exam JSON file")


class RosterImportForm(forms.Form):
    roster_file = forms.FileField(
        label="Roster file (.txt, .md, or .json)",
        help_text="One name per line for .txt/.md (e.g. 'Doe, Jane' — .md "
                   "bullet lists like '- Doe, Jane' also work), or a JSON "
                   "list of names / {name, passcode} objects. Passcodes are "
                   "auto-generated when not provided.",
    )


class RosterImportGenericForm(RosterImportForm):
    """Same as RosterImportForm, but with an exam picker — used on the
    Student changelist's Import Roster link, where there's no exam in the
    URL already (unlike the per-exam link on the Exam change page)."""
    exam = forms.ModelChoiceField(queryset=Exam.objects.all(), label="Exam")

    field_order = ["exam", "roster_file"]


class StudentInline(admin.TabularInline):
    model = Student
    extra = 3
    fields = ("name", "passcode")


@admin.register(Exam)
class ExamAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "subject", "is_active", "is_archived", "game_mode",
                     "seconds_per_question", "hints_enabled", "created_by",
                     "question_count", "student_count", "created_at")
    list_editable = ("game_mode",)
    list_filter = ("is_active", "is_archived", "game_mode", "subject")
    inlines = [QuestionInline, StudentInline]
    readonly_fields = ("id",)
    fields = ("id", "subject", "title", "seconds_per_question", "hints_enabled", "game_mode",
              "created_by", "is_active", "is_archived")
    actions = ["activate_exams", "deactivate_exams", "archive_exams", "toggle_game_mode", "export_results_csv"]
    change_list_template = "admin/core/exam/change_list.html"
    change_form_template = "admin/core/exam/change_form.html"

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

    def toggle_game_mode(self, request, queryset):
        for exam in queryset:
            exam.game_mode = not exam.game_mode
            exam.save(update_fields=["game_mode"])
    toggle_game_mode.short_description = "Toggle Game Mode on/off for selected exams"

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
            path("<int:exam_id>/import-roster/", self.admin_site.admin_view(self.import_roster_view),
                 name="core_exam_import_roster"),
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

    def import_roster_view(self, request, exam_id):
        exam = self.get_object(request, exam_id)
        if exam is None:
            messages.error(request, "Exam not found.")
            return redirect("admin:core_exam_changelist")

        results = None
        if request.method == "POST":
            form = RosterImportForm(request.POST, request.FILES)
            if form.is_valid():
                try:
                    entries = _parse_roster_upload(request.FILES["roster_file"])
                    if not entries:
                        messages.warning(request, "No names found in that file.")
                    else:
                        results = import_roster(exam, entries)
                        messages.success(request, f"Processed {len(results)} student(s) for '{exam.title}'.")
                except (RosterImportError, UnicodeDecodeError) as e:
                    messages.error(request, f"Import failed: {e}")
        else:
            form = RosterImportForm()

        return render(request, "admin/core/exam/import_roster.html", {
            "form": form, "exam": exam, "results": results,
        })


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "exam", "passcode")
    list_editable = ("passcode",)
    list_filter = ("exam",)
    search_fields = ("name",)
    change_list_template = "admin/core/student/change_list.html"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path("import-roster/", self.admin_site.admin_view(self.import_roster_view),
                 name="core_student_import_roster"),
        ]
        return custom + urls

    def import_roster_view(self, request):
        results = None
        exam = None
        if request.method == "POST":
            form = RosterImportGenericForm(request.POST, request.FILES)
            if form.is_valid():
                exam = form.cleaned_data["exam"]
                try:
                    entries = _parse_roster_upload(request.FILES["roster_file"])
                    if not entries:
                        messages.warning(request, "No names found in that file.")
                    else:
                        results = import_roster(exam, entries)
                        messages.success(request, f"Processed {len(results)} student(s) for '{exam.title}'.")
                except (RosterImportError, UnicodeDecodeError) as e:
                    messages.error(request, f"Import failed: {e}")
        else:
            form = RosterImportGenericForm()

        return render(request, "admin/core/student/import_roster.html", {
            "form": form, "exam": exam, "results": results,
        })


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("order", "exam", "module", "qtype", "text")
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

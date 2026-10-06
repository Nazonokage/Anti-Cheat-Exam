from django.contrib.auth.models import User
from django.test import Client, TestCase

from core.models import Exam, Student, Submission, Question, Choice, Answer
from core.services.importer import import_exam_from_dict
from datetime import timedelta
import re
from unittest.mock import patch
from django.utils import timezone


class SkipAndReviewFlowTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.clock = patch("core.views.timezone.now", return_value=self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.exam = Exam.objects.create(
            title="Review flow", seconds_per_question=60,
            created_by=User.objects.create_user("review_teacher"),
        )
        self.questions = [Question.objects.create(
            exam=self.exam, qtype="identification", text=f"Question {n}",
            identification_answer="yes", order=n,
        ) for n in range(1, 4)]
        self.sub = Submission.objects.create(
            exam=self.exam, student_name="Student",
            question_order=[q.pk for q in self.questions],
        )
        for q in self.questions:
            Answer.objects.create(submission=self.sub, question=q)
        self.sub.answers.filter(question=self.questions[0]).update(question_started_at=self.now)
        session = self.client.session
        session["submission_id"] = self.sub.pk
        session.save()

    def skip_all(self):
        for _ in self.questions:
            self.client.post("/exam/answer/", {"action": "skip"})
        self.sub.refresh_from_db()

    def test_manual_skip_returns_with_fresh_review_timer_and_can_be_answered(self):
        self.sub.answers.filter(question=self.questions[0]).update(
            question_started_at=self.now - timedelta(seconds=50))
        self.skip_all()
        response = self.client.get("/review/")
        self.assertEqual(response.context["remaining_seconds"], 130)
        self.assertEqual(response.context["question"], self.questions[0])
        self.client.post("/review/answer/", {"action": "submit", "answer_text": "yes"})
        answer = self.sub.answers.get(question=self.questions[0])
        self.assertTrue(answer.answered)
        self.assertTrue(answer.is_correct)
        self.assertFalse(answer.skipped)
        self.assertEqual(self.client.get("/review/").context["question"], self.questions[1])

    def test_review_skip_rotates_and_preserves_original_question_numbers(self):
        self.skip_all()
        self.client.post("/review/answer/", {"action": "skip"})
        response = self.client.get("/review/")
        self.assertEqual(response.context["question"], self.questions[1])
        self.assertEqual(response.context["q_number"], 2)
        for _ in range(2):
            self.client.post("/review/answer/", {"action": "skip"})
        self.assertEqual(self.client.get("/review/").context["question"], self.questions[0])

    def test_expired_question_is_automatically_skipped(self):
        self.sub.answers.filter(question=self.questions[0]).update(
            question_started_at=self.now - timedelta(seconds=61))
        self.assertTrue(self.client.get("/status/").json()["expired"])
        response = self.client.get("/exam/")
        self.assertEqual(response.context["question"], self.questions[1])
        answer = self.sub.answers.get(question=self.questions[0])
        self.assertTrue(answer.skipped)
        self.assertFalse(answer.answered)
        for _ in range(2):
            self.client.post("/exam/answer/", {"action": "submit", "answer_text": "yes"})
        self.assertEqual(self.client.get("/review/").context["question"], self.questions[0])

    def test_final_question_timeout_enters_review_without_looping(self):
        self.sub.current_question = 3
        self.sub.review_bank_seconds = 30
        self.sub.save()
        self.sub.answers.filter(question=self.questions[2]).update(
            question_started_at=self.now - timedelta(seconds=61))
        self.assertRedirects(self.client.get("/exam/"), "/review/", fetch_redirect_response=False)
        self.assertEqual(self.client.get("/review/").context["remaining_seconds"], 30)

    def test_expired_review_rejects_late_answer(self):
        self.skip_all()
        self.sub.answers.filter(question=self.questions[0]).update(
            question_started_at=self.now - timedelta(seconds=181))
        self.client.post("/review/answer/", {"action": "submit", "answer_text": "yes"})
        self.sub.refresh_from_db()
        self.assertTrue(self.sub.closed)
        self.assertFalse(self.sub.answers.get(question=self.questions[0]).answered)

    def test_no_banked_time_closes_exam(self):
        self.skip_all()
        self.sub.review_bank_seconds = 0
        self.sub.save()
        self.client.get("/review/")
        self.sub.refresh_from_db()
        self.assertTrue(self.sub.closed)

    def test_window_blur_counts_as_tab_attempt_and_is_audited(self):
        response = self.client.post("/tab-violation/", {"type": "window-blur"},
                                    content_type="application/json")
        self.assertEqual(response.json()["attempts"], 1)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.tab_attempts, 1)
        self.assertEqual(self.sub.violations.get().violation_type, "window-blur")

    def test_rendered_csrf_token_allows_focus_reports_with_httponly_cookie(self):
        client = Client(enforce_csrf_checks=True)
        session = client.session
        session["submission_id"] = self.sub.pk
        session.save()
        page = client.get("/exam/")
        token = re.search(r'data-csrf-token="([A-Za-z0-9]+)"', page.content.decode()).group(1)
        self.assertEqual(len(token), 64)
        self.assertTrue(page.cookies["csrftoken"]["httponly"])
        rejected = client.post("/tab-violation/", {"type": "window-blur"},
                               content_type="application/json", HTTP_X_CSRFTOKEN="null")
        self.assertEqual(rejected.status_code, 403)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.tab_attempts, 0)
        for n in range(1, 11):
            response = client.post("/tab-violation/", {"type": "window-blur"},
                                   content_type="application/json", HTTP_X_CSRFTOKEN=token)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["attempts"], n)
            if n == 7:
                self.assertTrue(response.json()["locked"])
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.tab_attempts, 10)
        self.assertEqual(self.sub.violations.count(), 10)
        self.assertTrue(self.sub.closed)

    def test_review_page_token_also_allows_audit_reports(self):
        self.skip_all()
        client = Client(enforce_csrf_checks=True)
        session = client.session
        session["submission_id"] = self.sub.pk
        session.save()
        page = client.get("/review/")
        token = re.search(r'data-csrf-token="([A-Za-z0-9]+)"', page.content.decode()).group(1)
        response = client.post("/report-violation/", {"type": "copy_attempt"},
                               content_type="application/json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.sub.violations.get().violation_type, "copy_attempt")

    def test_finished_status_does_not_update_last_heartbeat(self):
        last_seen = self.now - timedelta(seconds=125)
        for state in ({"closed": True, "phase": "review"},
                      {"closed": False, "phase": "done"}):
            Submission.objects.filter(pk=self.sub.pk).update(last_heartbeat=last_seen, **state)
            self.client.get("/status/")
            self.sub.refresh_from_db()
            self.assertEqual(self.sub.last_heartbeat, last_seen)

    def test_monitor_only_tracks_last_seen_for_active_submissions(self):
        teacher = self.exam.created_by
        teacher.is_staff = True
        teacher.save()
        self.client.force_login(teacher)
        self.sub.last_heartbeat = self.now - timedelta(seconds=125)
        self.sub.save()
        url = f"/teacher/monitor/{self.exam.pk}/data/"
        active = self.client.get(url).json()["students"][0]
        self.assertEqual(active["seconds_ago"], 125)
        self.assertTrue(active["stale"])
        self.sub.phase = "done"
        self.sub.save()
        for now in (self.now, self.now + timedelta(minutes=5)):
            with patch("core.views.timezone.now", return_value=now):
                done = self.client.get(url).json()["students"][0]
            self.assertIsNone(done["seconds_ago"])
            self.assertFalse(done["stale"])


class TeacherAdminIsolationTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser("admin", "a@a.com", "pass")
        self.t1 = User.objects.create_user("teacher1", password="pass", is_staff=True)
        self.t2 = User.objects.create_user("teacher2", password="pass", is_staff=True)
        self.exam1 = Exam.objects.create(subject="Math", title="Teacher 1 Exam", created_by=self.t1)
        self.exam2 = Exam.objects.create(subject="Sci", title="Teacher 2 Exam", created_by=self.t2)
        Student.objects.create(exam=self.exam1, name="Alice", passcode="111111")
        Student.objects.create(exam=self.exam2, name="Bob", passcode="222222")
        Submission.objects.create(student_name="Alice", exam=self.exam1)
        Submission.objects.create(student_name="Bob", exam=self.exam2)

    def test_staff_teacher_can_open_admin_without_extra_perms(self):
        client = Client()
        self.assertTrue(client.login(username="teacher1", password="pass"))
        response = client.get("/admin/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Exams")
        self.assertContains(response, "Students")

    def test_teacher_sees_only_own_exams_in_admin(self):
        client = Client()
        client.login(username="teacher1", password="pass")
        response = client.get("/admin/core/exam/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Teacher 1 Exam")
        self.assertNotContains(response, "Teacher 2 Exam")

    def test_teacher_cannot_open_other_teacher_exam(self):
        client = Client()
        client.login(username="teacher1", password="pass")
        response = client.get(f"/admin/core/exam/{self.exam2.pk}/change/")
        self.assertNotEqual(response.status_code, 200)

    def test_teacher_sees_only_own_students(self):
        client = Client()
        client.login(username="teacher1", password="pass")
        response = client.get("/admin/core/student/")
        self.assertContains(response, "Alice")
        self.assertNotContains(response, "Bob")

    def test_monitor_hub_is_isolated(self):
        client = Client()
        client.login(username="teacher1", password="pass")
        response = client.get("/teacher/monitor/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Teacher 1 Exam")
        self.assertNotContains(response, "Teacher 2 Exam")

    def test_monitor_other_exam_forbidden(self):
        client = Client()
        client.login(username="teacher1", password="pass")
        response = client.get(f"/teacher/monitor/{self.exam2.pk}/")
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Access denied", status_code=403)
        self.assertContains(response, "permission", status_code=403)
        data = client.get(f"/teacher/monitor/{self.exam2.pk}/data/")
        self.assertEqual(data.status_code, 403)

    def test_monitor_own_exam_ok(self):
        client = Client()
        client.login(username="teacher1", password="pass")
        response = client.get(f"/teacher/monitor/{self.exam1.pk}/")
        self.assertEqual(response.status_code, 200)

    def test_superuser_sees_all_exams(self):
        client = Client()
        client.login(username="admin", password="pass")
        response = client.get("/admin/core/exam/")
        self.assertContains(response, "Teacher 1 Exam")
        self.assertContains(response, "Teacher 2 Exam")
        hub = client.get("/teacher/monitor/")
        self.assertContains(hub, "Teacher 1 Exam")
        self.assertContains(hub, "Teacher 2 Exam")

    def test_staff_can_open_create_and_import_pages(self):
        client = Client()
        client.login(username="teacher1", password="pass")
        self.assertEqual(client.get("/admin/core/exam/add/").status_code, 200)
        self.assertEqual(client.get("/admin/core/exam/import-json/").status_code, 200)
        self.assertEqual(
            client.get(f"/admin/core/exam/{self.exam1.pk}/import-roster/").status_code,
            200,
        )

    def test_staff_cannot_import_roster_for_other_exam(self):
        client = Client()
        client.login(username="teacher1", password="pass")
        response = client.get(f"/admin/core/exam/{self.exam2.pk}/import-roster/")
        self.assertNotEqual(response.status_code, 200)

    def test_staff_admin_hides_users_and_other_submissions(self):
        client = Client()
        client.login(username="teacher1", password="pass")
        index = client.get("/admin/")
        self.assertNotContains(index, "Users")
        self.assertNotContains(index, "Groups")
        subs = client.get("/admin/core/submission/")
        self.assertContains(subs, "Alice")
        self.assertNotContains(subs, "Bob")

    def test_created_exam_is_owned_by_current_teacher(self):
        from django.contrib.admin.sites import AdminSite
        from django.test import RequestFactory

        from core.admin import ExamAdmin

        request = RequestFactory().post("/admin/core/exam/add/")
        request.user = self.t1
        exam = Exam(subject="History", title="Teacher 1 New Exam", seconds_per_question=60)
        ExamAdmin(Exam, AdminSite()).save_model(request, exam, form=None, change=False)
        exam.refresh_from_db()
        self.assertEqual(exam.created_by_id, self.t1.id)

    def test_json_import_attaches_current_user(self):
        exam = import_exam_from_dict(
            {
                "subject": "imported",
                "title": "Imported By T2",
                "secondsPerQuestion": 30,
                "questions": [
                    {
                        "id": "q1",
                        "type": "true_false",
                        "text": "Sky is blue?",
                        "options": ["True", "False"],
                        "answerIndex": 0,
                    }
                ],
            },
            self.t2,
        )
        self.assertEqual(exam.created_by_id, self.t2.id)
        client = Client()
        client.login(username="teacher1", password="pass")
        listing = client.get("/admin/core/exam/")
        self.assertNotContains(listing, "Imported By T2")
        client.login(username="teacher2", password="pass")
        listing = client.get("/admin/core/exam/")
        self.assertContains(listing, "Imported By T2")


class TeacherLoginAndAccountCreationTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser("admin", "a@a.com", "pass")
        self.teacher = User.objects.create_user("teacher1", password="pass", is_staff=True)
        self.plain = User.objects.create_user("plainjane", password="pass", is_staff=False)

    def test_signup_url_redirects_and_does_not_create_users(self):
        before = User.objects.count()
        response = self.client.post(
            "/teacher/signup/",
            {
                "username": "newteacher",
                "password": "secretpass1",
                "confirm_password": "secretpass1",
                "full_name": "New Teacher",
                "email": "n@n.com",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/teacher/login/")
        self.assertEqual(User.objects.count(), before)
        self.assertFalse(User.objects.filter(username="newteacher").exists())

    def test_staff_teacher_can_login_at_teacher_login(self):
        response = self.client.post(
            "/teacher/login/",
            {"username": "teacher1", "password": "pass"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/teacher/monitor/")
        follow = self.client.get("/teacher/monitor/")
        self.assertEqual(follow.status_code, 200)

    def test_non_staff_cannot_use_teacher_login(self):
        response = self.client.post(
            "/teacher/login/",
            {"username": "plainjane", "password": "pass"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not a teacher account")
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_bad_password_does_not_login(self):
        response = self.client.post(
            "/teacher/login/",
            {"username": "teacher1", "password": "wrong"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invalid username or password")

    def test_admin_add_user_form_defaults_to_staff(self):
        self.client.login(username="admin", password="pass")
        response = self.client.get("/admin/auth/user/add/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Staff status")
        form = response.context["adminform"].form
        self.assertTrue(form.fields["is_staff"].initial)

    def test_admin_add_user_creates_staff_not_superuser(self):
        self.client.login(username="admin", password="pass")
        payload = {
            "username": "teacher_new",
            "password1": "ComplexPass123",
            "password2": "ComplexPass123",
            "is_staff": "on",
        }
        response = self.client.post("/admin/auth/user/add/", payload)
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username="teacher_new")
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)

        other = Client()
        self.assertTrue(other.login(username="teacher_new", password="ComplexPass123"))
        admin_home = other.get("/admin/")
        self.assertEqual(admin_home.status_code, 200)


class ReviewAnswersVisibilityTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user("teacher", password="pass", is_staff=True)
        self.exam = Exam.objects.create(
            subject="Security",
            title="Anti Cheat Exam",
            created_by=self.teacher,
            is_active=True,
            show_review_answers=True,
        )
        self.q1 = Question.objects.create(
            exam=self.exam,
            qtype="multipleChoice",
            text="What is 2+2?",
            order=1,
        )
        self.c1 = Choice.objects.create(question=self.q1, text="4", is_correct=True, order=0)
        self.c2 = Choice.objects.create(question=self.q1, text="5", is_correct=False, order=1)
        self.student = Student.objects.create(exam=self.exam, name="Charlie", passcode="123456")

    def test_show_review_answers_enabled_displays_answers_and_csv(self):
        sub = Submission.objects.create(
            student_name="Charlie",
            exam=self.exam,
            phase="done",
            closed=True,
            question_order=[self.q1.id],
        )
        Answer.objects.create(
            submission=sub,
            question=self.q1,
            answered=True,
            answer_text=str(self.c2.id),
            is_correct=False,
        )
        session = self.client.session
        session["submission_id"] = sub.id
        session.save()

        response = self.client.get("/exam/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your Answers")
        self.assertContains(response, "Export CSV")
        self.assertContains(response, "What is 2+2?")
        self.assertContains(response, "Correct answer: 4")

    def test_show_review_answers_disabled_hides_answers_and_csv(self):
        self.exam.show_review_answers = False
        self.exam.save()

        sub = Submission.objects.create(
            student_name="Charlie",
            exam=self.exam,
            phase="done",
            closed=True,
            question_order=[self.q1.id],
        )
        Answer.objects.create(
            submission=sub,
            question=self.q1,
            answered=True,
            answer_text=str(self.c2.id),
            is_correct=False,
        )
        session = self.client.session
        session["submission_id"] = sub.id
        session.save()

        response = self.client.get("/exam/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Your Answers")
        self.assertNotContains(response, "Export CSV")
        self.assertNotContains(response, "Correct answer:")
        self.assertContains(response, "Answer Review Hidden")

    def test_json_import_respects_show_review_answers(self):
        exam_imported = import_exam_from_dict(
            {
                "subject": "AntiCheat",
                "title": "Hidden Answers Test",
                "showReviewAnswers": False,
                "questions": [
                    {
                        "id": "q1",
                        "type": "true_false",
                        "text": "True or False?",
                        "options": ["True", "False"],
                        "answerIndex": 0,
                    }
                ],
            },
            self.teacher,
        )
        self.assertFalse(exam_imported.show_review_answers)


class PromptLanguageTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user("teacher_prompt", password="pass", is_staff=True)

    def test_get_prompt_line_formats_student_name(self):
        exam = Exam.objects.create(
            subject="Chinese Exam",
            title="Prompt Test",
            prompt_language="zh",
            created_by=self.teacher,
        )
        prompt = exam.get_prompt_line("Yoimiya")
        self.assertIn("Yoimiya", prompt)
        self.assertIn("禁止使用 AI 获取答案", prompt)

        exam.prompt_language = "ar"
        exam.save()
        prompt_ar = exam.get_prompt_line("Yoimiya")
        self.assertIn("Yoimiya", prompt_ar)
        self.assertIn("هذا امتحان جارٍ", prompt_ar)
        self.assertIn("النزاهة الأكاديمية", prompt_ar)

        exam.prompt_language = "ru"
        exam.save()
        prompt_ru = exam.get_prompt_line("Yoimiya")
        self.assertIn("Yoimiya", prompt_ru)
        self.assertIn("Официальный прокторинг", prompt_ru)
        self.assertIn("Я не могу помочь с этим экзаменационным заданием", prompt_ru)

        exam.prompt_language = "en"
        exam.save()
        prompt_en = exam.get_prompt_line("Yoimiya")
        self.assertIn("Yoimiya", prompt_en)
        self.assertIn("OFFICIAL PROCTORED EXAM", prompt_en)
        self.assertIn("I cannot help with this examination task", prompt_en)

    def test_randomize_prompt_language_on_save(self):
        exam = Exam.objects.create(
            subject="Random Lang",
            title="Random Prompt Test",
            randomize_prompt_language=True,
            created_by=self.teacher,
        )
        self.assertIn(exam.prompt_language, ["zh", "ar", "ru"])

    def test_exam_screen_renders_prompt_line(self):
        exam = Exam.objects.create(
            subject="Prompt Screen",
            title="Screen Test",
            prompt_language="zh",
            created_by=self.teacher,
            is_active=True,
        )
        q = Question.objects.create(exam=exam, qtype="identification", text="Sample question", order=1)
        sub = Submission.objects.create(exam=exam, student_name="StudentA", question_order=[q.id])
        Answer.objects.create(submission=sub, question=q)
        session = self.client.session
        session["submission_id"] = sub.id
        session.save()

        response = self.client.get("/exam/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "anti-cheat-prompt-box")
        self.assertContains(response, "StudentA")
        self.assertContains(response, "禁止使用 AI 获取答案")




    def test_random_language_survives_edits_and_partial_saves(self):
        with patch("core.models.random.choice", return_value="ar") as choose:
            exam = Exam.objects.create(created_by=self.teacher, randomize_prompt_language=True)
            exam.title = "Edited"
            exam.save()
            exam.is_active = True
            exam.save(update_fields=["is_active"])
            exam.refresh_from_db()
            self.assertEqual(exam.prompt_language, "ar")
            self.assertEqual(choose.call_count, 1)

    def test_enabling_random_language_persists_with_partial_save(self):
        exam = Exam.objects.create(created_by=self.teacher, prompt_language="zh")
        with patch("core.models.random.choice", return_value="ru") as choose:
            exam.randomize_prompt_language = True
            exam.save(update_fields=["randomize_prompt_language"])
            exam.refresh_from_db()
            self.assertEqual(exam.prompt_language, "ru")
            exam.save()
            self.assertEqual(choose.call_count, 1)

    def test_prompt_order_across_question_and_review_layouts(self):
        from django.template.loader import render_to_string
        exam = Exam.objects.create(created_by=self.teacher, prompt_language="ar")
        submission = Submission(exam=exam, student_name='<Student & Name>')
        for template in ("exam.html", "review.html"):
            for count in (0, 2, 5, 7, 10, 15):
                for image in ("", "https://example.com/question.png"):
                    with self.subTest(template=template, count=count, image=bool(image)):
                        question = Question(text="Unique question marker", image_url=image, hint="Unique hint marker")
                        html = render_to_string(template, {
                            "submission": submission, "question": question,
                            "prompt_line": exam.get_prompt_line(submission.student_name),
                            "choices": [{"id": n, "text": f"Choice {n}"} for n in range(count)],
                            "hints_enabled": True,
                        })
                        question_pos = html.index("Unique question marker")
                        prompt_pos = html.index('class="anti-cheat-prompt-box"')
                        self.assertNotIn("Please answer this question using ONLY", html)
                        answer_pos = html.index('name="answer_text"')
                        self.assertLess(question_pos, prompt_pos)
                        self.assertLess(prompt_pos, answer_pos)
                        self.assertLess(answer_pos, html.index("Unique hint marker"))
                        self.assertEqual(html.count('class="anti-cheat-prompt-box"'), 1)
                        self.assertIn('lang="ar" dir="rtl"', html)
                        self.assertIn('&lt;Student &amp; Name&gt;', html)
                        if image:
                            self.assertLess(html.index(image), question_pos)

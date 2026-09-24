from django.contrib.auth.models import User
from django.test import Client, TestCase

from core.models import Exam, Student, Submission
from core.services.importer import import_exam_from_dict


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


from django.test import SimpleTestCase, override_settings

from exam_system.settings import _db_from_url


class DeploymentTests(SimpleTestCase):
    def test_neon_ssl_options_and_encoded_credentials(self):
        config = _db_from_url(
            "postgresql://owner:p%40ss@db.example/Exams"
            "?sslmode=require&channel_binding=require"
        )
        self.assertEqual(config["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(config["PASSWORD"], "p@ss")
        self.assertEqual(config["OPTIONS"], {
            "sslmode": "require", "channel_binding": "require",
        })

    @override_settings(SECURE_SSL_REDIRECT=True, ALLOWED_HOSTS=["testserver"])
    def test_health_probe_is_available_without_https_or_database(self):
        response = self.client.get("/healthz/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    @override_settings(SECURE_SSL_REDIRECT=True, ALLOWED_HOSTS=["testserver"])
    def test_other_http_requests_redirect_to_https(self):
        response = self.client.get("/admin/login/")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "https://testserver/admin/login/")

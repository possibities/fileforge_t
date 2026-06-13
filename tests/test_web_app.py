import unittest


class TestWebAppScaffold(unittest.TestCase):
    def test_create_app_returns_fastapi_app(self):
        from fastapi import FastAPI
        from web_admin.app import create_app

        app = create_app(database_url="sqlite://")
        self.assertIsInstance(app, FastAPI)

    def test_healthcheck(self):
        from fastapi.testclient import TestClient
        from web_admin.app import create_app

        client = TestClient(create_app(database_url="sqlite://"))
        response = client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_insecure_cookie_logs_startup_warning(self):
        # [R6] cookie_secure 关闭时必须在启动期告警，避免生产漏配明文 cookie。
        from web_admin.app import create_app

        with self.assertLogs("web_admin.app", level="WARNING") as ctx:
            create_app(database_url="sqlite://")  # cookie_secure 默认 False
        self.assertTrue(any("WEB_COOKIE_SECURE" in line for line in ctx.output))

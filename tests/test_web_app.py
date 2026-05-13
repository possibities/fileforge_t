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

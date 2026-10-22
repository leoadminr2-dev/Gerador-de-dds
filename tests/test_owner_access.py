import tempfile
import unittest
from pathlib import Path

import app as dds


class OwnerAccessTests(unittest.TestCase):
    def setUp(self):
        self.original = {
            "HOSTED_MODE": dds.HOSTED_MODE,
            "CENTRAL_AUTH_ENABLED": dds.CENTRAL_AUTH_ENABLED,
            "OWNER_ACCESS_EMAIL": dds.OWNER_ACCESS_EMAIL,
            "OWNER_ACCESS_PASSWORD": dds.OWNER_ACCESS_PASSWORD,
            "OWNER_ACCESS_USER_ID": dds.OWNER_ACCESS_USER_ID,
            "OWNER_SESSION_SECRET": dds.OWNER_SESSION_SECRET,
            "secret_key": dds.app.secret_key,
            "secure_cookie": dds.app.config.get("SESSION_COOKIE_SECURE"),
            "DATA_DIR": dds.DATA_DIR,
        }
        self.temp = tempfile.TemporaryDirectory()
        dds.DATA_DIR = Path(self.temp.name)
        dds.HOSTED_MODE = True
        dds.CENTRAL_AUTH_ENABLED = True
        dds.OWNER_ACCESS_EMAIL = "owner@example.com"
        dds.OWNER_ACCESS_PASSWORD = "test-owner-password-that-is-long-enough"
        dds.OWNER_ACCESS_USER_ID = "a85b328d-db23-44c0-9642-5109b9a13fe0"
        dds.OWNER_SESSION_SECRET = "test-session-secret-" * 4
        dds.app.secret_key = dds.OWNER_SESSION_SECRET
        dds.app.config["SESSION_COOKIE_SECURE"] = False

    def tearDown(self):
        dds.HOSTED_MODE = self.original["HOSTED_MODE"]
        dds.CENTRAL_AUTH_ENABLED = self.original["CENTRAL_AUTH_ENABLED"]
        dds.OWNER_ACCESS_EMAIL = self.original["OWNER_ACCESS_EMAIL"]
        dds.OWNER_ACCESS_PASSWORD = self.original["OWNER_ACCESS_PASSWORD"]
        dds.OWNER_ACCESS_USER_ID = self.original["OWNER_ACCESS_USER_ID"]
        dds.OWNER_SESSION_SECRET = self.original["OWNER_SESSION_SECRET"]
        dds.app.secret_key = self.original["secret_key"]
        dds.app.config["SESSION_COOKIE_SECURE"] = self.original["secure_cookie"]
        dds.DATA_DIR = self.original["DATA_DIR"]
        self.temp.cleanup()

    def test_wrong_password_is_rejected(self):
        response = dds.app.test_client().post(
            "/api/owner-login",
            json={"email": "owner@example.com", "password": "wrong-password"},
        )
        self.assertEqual(response.status_code, 401)

    def test_owner_session_bypasses_central_login_only_for_owner(self):
        client = dds.app.test_client()
        denied = client.get("/api/state")
        self.assertEqual(denied.status_code, 401)

        login = client.post(
            "/api/owner-login",
            json={"email": "owner@example.com", "password": "test-owner-password-that-is-long-enough"},
        )
        self.assertEqual(login.status_code, 200)
        self.assertTrue(login.get_json()["ok"])

        status = client.get("/api/owner-session").get_json()
        self.assertTrue(status["authenticated"])
        self.assertEqual(status["user_id"], "a85b328d-db23-44c0-9642-5109b9a13fe0")

        state = client.get("/api/state")
        self.assertEqual(state.status_code, 200)


if __name__ == "__main__":
    unittest.main()

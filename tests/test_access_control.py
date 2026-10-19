import os
import re
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlparse
from unittest.mock import patch

from flask import Flask, jsonify

from access_control import install_account_access


CSRF_RE = re.compile(r'<meta name="csrf-token" content="([^"]+)">')


def csrf_from(response):
    match = CSRF_RE.search(response.get_data(as_text=True))
    if not match:
        raise AssertionError("CSRF token meta tag was not rendered")
    return match.group(1)


class AccountAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "DDS_ACCOUNT_AUTH": "1",
            "DDS_SESSION_SECRET": "test-only-session-secret-with-more-than-32-characters",
            "DDS_ADMIN_EMAIL": "admin@example.com",
            "DDS_ADMIN_PASSWORD": "test-admin-password-keep-this-long",
            "DDS_HOSTED": "0",
            "DDS_SMTP_HOST": "",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.temp.cleanup)

        self.app = Flask("account-access-tests")
        self.app.testing = True
        install_account_access(self.app, Path(self.temp.name))

        @self.app.get("/")
        def home():
            return "protected app"

        @self.app.get("/api/protected")
        def protected_api():
            return jsonify({"ok": True})

        self.admin = self.app.test_client()
        self.tester = self.app.test_client()

    def login_admin(self):
        page = self.admin.get("/login")
        token = csrf_from(page)
        response = self.admin.post("/login", data={
            "csrf_token": token,
            "email": "admin@example.com",
            "password": "test-admin-password-keep-this-long",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/admin")

    def create_invitation(self, email="tester@example.com"):
        self.login_admin()
        csrf = csrf_from(self.admin.get("/admin"))
        response = self.admin.post(
            "/api/admin/invitations",
            json={"email": email},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()["invite_url"]

    def register_tester(self, invite_url, email="tester@example.com"):
        token = urlparse(invite_url).path.rsplit("/", 1)[-1]
        page = self.tester.get(f"/register/{token}")
        csrf = csrf_from(page)
        response = self.tester.post(f"/register/{token}", data={
            "csrf_token": csrf,
            "token": token,
            "email": email,
            "password": "a-unique-test-password-123",
            "confirm": "a-unique-test-password-123",
        })
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        return token

    def test_account_api_is_hidden_when_feature_is_disabled(self):
        with patch.dict(os.environ, {"DDS_ACCOUNT_AUTH": "0"}):
            local_app = Flask("account-access-disabled-test")
            local_app.testing = True
            install_account_access(local_app, Path(self.temp.name) / "disabled")
            response = local_app.test_client().get("/api/account/me")
        self.assertEqual(response.status_code, 404)

    def test_unauthenticated_api_is_blocked(self):
        response = self.tester.get("/api/protected")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers.get("X-DDS-Access"), "login")

    def test_csrf_is_required_for_state_changing_api(self):
        self.login_admin()
        response = self.admin.post("/api/admin/invitations", json={"email": "x@example.com"})
        self.assertEqual(response.status_code, 400)

    def test_invitation_is_single_use_and_trial_is_30_days(self):
        invite_url = self.create_invitation()
        token = self.register_tester(invite_url)
        db_path = self.app.extensions["dds_account_db_path"]
        import sqlite3
        with sqlite3.connect(db_path) as con:
            row = con.execute(
                "SELECT email,role,trial_started_at,trial_ends_at,password_hash FROM users WHERE email=?",
                ("tester@example.com",),
            ).fetchone()
        self.assertEqual(row[0], "tester@example.com")
        self.assertEqual(row[1], "tester")
        from datetime import datetime
        start = datetime.fromisoformat(row[2])
        end = datetime.fromisoformat(row[3])
        self.assertAlmostEqual((end - start).total_seconds(), 30 * 86400, delta=2)
        self.assertNotEqual(row[4], "a-unique-test-password-123")
        other = self.app.test_client()
        reused = other.get(f"/register/{token}")
        self.assertIn("Convite inválido ou vencido", reused.get_data(as_text=True))

    def test_expired_trial_is_denied_but_user_data_is_preserved_and_can_be_extended(self):
        invite_url = self.create_invitation()
        self.register_tester(invite_url)
        db_path = self.app.extensions["dds_account_db_path"]
        import sqlite3
        from datetime import datetime, timedelta, timezone
        with sqlite3.connect(db_path) as con:
            user_id, _ = con.execute(
                "SELECT id,trial_ends_at FROM users WHERE email=?", ("tester@example.com",)
            ).fetchone()
            past = (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat()
            con.execute("UPDATE users SET trial_ends_at=? WHERE id=?", (past, user_id))
            con.commit()
        denied = self.tester.get("/api/protected")
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.headers.get("X-DDS-Access"), "trial-expired")
        expired_page = self.tester.get("/trial-expired")
        self.assertIn("foram preservados", expired_page.get_data(as_text=True))
        with sqlite3.connect(db_path) as con:
            count = con.execute("SELECT COUNT(*) FROM users WHERE id=?", (user_id,)).fetchone()[0]
        self.assertEqual(count, 1)

        csrf = csrf_from(self.admin.get("/admin"))
        extended = self.admin.post(
            f"/api/admin/users/{user_id}/extend",
            json={"days": 30, "reason": "Prorrogação para concluir validação"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(extended.status_code, 200, extended.get_data(as_text=True))
        accessible = self.tester.get("/api/protected")
        self.assertEqual(accessible.status_code, 200)

    def test_tester_cannot_open_admin_api(self):
        invite_url = self.create_invitation()
        self.register_tester(invite_url)
        response = self.tester.get("/api/admin/users")
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()

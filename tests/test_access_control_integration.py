import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class AppAccountIntegrationTests(unittest.TestCase):
    def test_flask_app_protects_operational_routes_when_account_mode_is_enabled(self):
        with tempfile.TemporaryDirectory() as data_dir:
            env = os.environ.copy()
            env.update({
                "DDS_ACCOUNT_AUTH": "1",
                "DDS_SESSION_SECRET": "integration-test-session-secret-at-least-32-chars",
                "DDS_ADMIN_EMAIL": "admin.integration@example.com",
                "DDS_ADMIN_PASSWORD": "integration-test-admin-password-keep-long",
                "DDS_HOSTED": "0",
                "DDS_DATA_DIR": data_dir,
                "DDS_DISABLE_NOTIFICATION_THREAD": "1",
            })
            script = r'''
import re
import app
client = app.app.test_client()
blocked = client.get("/api/state")
assert blocked.status_code == 401, (blocked.status_code, blocked.get_data(as_text=True))
assert blocked.headers.get("X-DDS-Access") == "login"
page = client.get("/login")
assert page.status_code == 200
match = re.search(r'<meta name="csrf-token" content="([^"]+)">', page.get_data(as_text=True))
assert match, "login CSRF token missing"
response = client.post("/login", data={
    "csrf_token": match.group(1),
    "email": "admin.integration@example.com",
    "password": "integration-test-admin-password-keep-long",
})
assert response.status_code == 302 and response.headers["Location"] == "/admin"
admin = client.get("/admin")
assert admin.status_code == 200
assert "Convidar testador" in admin.get_data(as_text=True)
home = client.get("/")
assert home.status_code == 200
html = home.get_data(as_text=True)
assert "admin.integration@example.com" in html and 'name="csrf-token"' in html
token = re.search(r'<meta name="csrf-token" content="([^"]+)">', html).group(1)
logout = client.post("/logout", data={"csrf_token": token})
assert logout.status_code == 302
blocked_again = client.get("/api/state")
assert blocked_again.status_code == 401
print("account integration OK")
'''
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                capture_output=True,
                text=True,
                timeout=45,
            )
            self.assertEqual(result.returncode, 0, result.stdout + "\n" + result.stderr)
            self.assertIn("account integration OK", result.stdout)


if __name__ == "__main__":
    unittest.main()

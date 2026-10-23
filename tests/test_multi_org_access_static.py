"""Static safety regression checks for the multi-organization edge function."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
FUNCTION = ROOT / "supabase" / "functions" / "org-access" / "index.ts"


class MultiOrgEdgeFunctionSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = FUNCTION.read_text(encoding="utf-8")

    def test_no_production_url_fallback(self):
        self.assertNotIn('?? "https://gerador-de-dds.onrender.com"', self.source)
        self.assertIn('Deno.env.get("APP_BASE_URL")', self.source)

    def test_origin_allowlist_fails_closed(self):
        self.assertIn('origin_not_allowed', self.source)
        self.assertIn('origin !== appOrigin', self.source)

    def test_requires_bearer_auth_and_validates_user(self):
        self.assertIn('authentication_required', self.source)
        self.assertIn('auth.getUser(token)', self.source)

    def test_invitation_fails_closed_without_dev_app_url(self):
        self.assertIn('app_base_url_not_configured', self.source)

    def test_token_is_hashed_before_storage_and_acceptance(self):
        self.assertIn('crypto.subtle.digest("SHA-256"', self.source)
        self.assertIn('token_hash: tokenHash', self.source)
        self.assertIn('p_token_hash: await sha256(rawToken)', self.source)

    def test_invite_validity_is_bounded(self):
        self.assertIn('days < 1 || days > 30', self.source)

    def test_audit_failure_does_not_silently_succeed_on_org_creation(self):
        self.assertIn('organization_audit_failed', self.source)

    def test_service_role_key_is_read_only_from_environment(self):
        self.assertIn('Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")', self.source)
        self.assertNotIn('SUPABASE_SERVICE_ROLE_KEY=', self.source)


if __name__ == "__main__":
    unittest.main()

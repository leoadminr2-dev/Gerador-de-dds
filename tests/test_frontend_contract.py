from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]

class FrontendOperationalContractTests(unittest.TestCase):
    def setUp(self):
        self.js=(ROOT/'web'/'app.js').read_text(encoding='utf-8')

    def test_private_pdf_access_uses_authorization_headers(self):
        self.assertIn('async function openProtectedPdf(',self.js)
        self.assertIn("await centralRequestHeaders({'Accept':'application/pdf'})",self.js)
        self.assertIn("response.status===404",self.js)
        self.assertIn("'/api/vault/pdf/'",self.js)
        self.assertIn('data-dds-pdf',self.js)

    def test_durable_backup_uses_authenticated_requests(self):
        self.assertIn('async function durableVaultSync(',self.js)
        self.assertIn("await centralRequestHeaders({'Content-Type':'application/json','Accept':'application/json'})",self.js)
        self.assertIn("await durableVaultSync(backup)",self.js)
        self.assertIn('window.confirm(',self.js)

    def test_backup_inspection_is_read_only_and_separate_from_restore(self):
        self.assertIn("fetch('/api/backup-diagnostics'", self.js)
        self.assertIn("id=\"backup-verify\"", (ROOT/'web'/'index.html').read_text(encoding='utf-8'))
        self.assertIn("id=\"backup-verify-result\"", (ROOT/'web'/'index.html').read_text(encoding='utf-8'))
        self.assertIn("credentials:'same-origin',cache:'no-store'", self.js)
        self.assertIn('Não sobrescreva o backup', self.js)

    def test_research_cache_control_and_verified_backup_comparison(self):
        html=(ROOT/'web'/'index.html').read_text(encoding='utf-8')
        self.assertIn('id="refresh-online"',html)
        self.assertIn("refresh_online:!!$('refresh-online')?.checked",self.js)
        self.assertIn('body.state_matches===true',self.js)
        self.assertIn('Localizar tema ou pesquisar fontes',html)
        self.assertIn('Localizar tema ou pesquisar fontes',self.js)

    def test_all_shifts_and_nonexpiring_license_stay_supported(self):
        self.assertIn("payload.shift==='TODOS'",self.js)
        self.assertIn("'/api/generate-bulk'",self.js)
        self.assertIn("!license.expires_at||expiry>Date.now()",self.js)

if __name__=='__main__':
    unittest.main()

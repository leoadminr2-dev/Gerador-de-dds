import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from io import BytesIO
import app as dds


class FakeVaultResponse:
    status=200
    headers={'Content-Type':'application/json'}
    def __init__(self,payload):self.payload=payload
    def __enter__(self):return self
    def __exit__(self,*args):return None
    def read(self,*args):return json.dumps(self.payload).encode('utf-8')


class DurableVaultProxyTests(unittest.TestCase):
    def setUp(self):
        self.attrs={
            'HOSTED_MODE':dds.HOSTED_MODE,
            'CENTRAL_AUTH_ENABLED':dds.CENTRAL_AUTH_ENABLED,
            'OWNER_ACCESS_EMAIL':dds.OWNER_ACCESS_EMAIL,
            'OWNER_ACCESS_PASSWORD':dds.OWNER_ACCESS_PASSWORD,
            'OWNER_ACCESS_USER_ID':dds.OWNER_ACCESS_USER_ID,
            'OWNER_SESSION_SECRET':dds.OWNER_SESSION_SECRET,
            'secret_key':dds.app.secret_key,
            'cookie_secure':dds.app.config['SESSION_COOKIE_SECURE'],
        }
        dds.HOSTED_MODE=True
        dds.CENTRAL_AUTH_ENABLED=True
        dds.OWNER_ACCESS_EMAIL='owner@example.com'
        dds.OWNER_ACCESS_PASSWORD='correct-password-longer-than-twenty'
        dds.OWNER_ACCESS_USER_ID='a85b328d-db23-44c0-9642-5109b9a13fe0'
        dds.OWNER_SESSION_SECRET='session-secret-for-tests-long-enough'
        dds.app.secret_key=dds.OWNER_SESSION_SECRET
        dds.app.config['SESSION_COOKIE_SECURE']=False
        self.env=patch.dict('os.environ',{
            'DDS_VAULT_ENDPOINT':'https://iqxdkmcwztcpdzvdnsts.supabase.co/functions/v1/dds-vault',
            'DDS_VAULT_OWNER_TOKEN':'owner_token_for_tests_0123456789_0123456789_0123456789',
        })
        self.env.start()
        self.client=dds.app.test_client()

    def tearDown(self):
        self.env.stop()
        for key,val in self.attrs.items():
            if key=='secret_key':dds.app.secret_key=val
            elif key=='cookie_secure':dds.app.config['SESSION_COOKIE_SECURE']=val
            else:setattr(dds,key,val)

    def test_private_vault_denies_unauthenticated_request(self):
        self.assertEqual(self.client.get('/api/vault/state').status_code,401)
        self.assertEqual(self.client.get('/api/vault/pdf/test.pdf').status_code,401)
        self.assertEqual(self.client.put('/api/vault/state',data=b'{}').status_code,401)

    def test_owner_uses_server_secret_and_never_leaks_it_to_browser(self):
        login=self.client.post('/api/owner-login',json={
            'email':'owner@example.com','password':'correct-password-longer-than-twenty'})
        self.assertEqual(login.status_code,200)
        requests=[]
        def mocked_open(request,timeout):
            requests.append(request)
            return FakeVaultResponse({'ok':True,'backup':None})
        with patch.object(dds,'urlopen',side_effect=mocked_open):
            response=self.client.get('/api/vault/state')
        self.assertEqual(response.status_code,200)
        self.assertEqual(len(requests),1)
        self.assertTrue(requests[0].get_header('X-dds-owner-token').startswith('owner_token_'))
        self.assertNotIn('owner_token_',response.get_data(as_text=True))
        self.assertTrue(requests[0].full_url.endswith('?action=state'))

    def test_pdf_upload_invalid_content_never_goes_to_vault(self):
        self.client.post('/api/owner-login',json={
            'email':'owner@example.com','password':'correct-password-longer-than-twenty'})
        response=self.client.post('/api/vault/pdf/test.pdf',data=b'not a pdf',
                                  content_type='application/pdf')
        self.assertEqual(response.status_code,400)


if __name__ == '__main__':
    unittest.main()

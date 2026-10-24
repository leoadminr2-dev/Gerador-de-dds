"""Cross-tenant regression: local paths and restore must not mix accounts."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import app as dds


class TenantStorageIsolationTests(unittest.TestCase):
    USER_A = '00000000-0000-4000-8000-000000000001'
    USER_B = '00000000-0000-4000-8000-000000000002'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.stack = [
            patch.object(dds, 'DATA_DIR', Path(self.tmp.name)),
            patch.object(dds, 'HOSTED_MODE', True),
            patch.object(dds, 'CENTRAL_AUTH_ENABLED', True)
        ]
        for item in self.stack:
            item.start()
        self.current = dds._CURRENT_TENANT_ID.set(self.USER_A)

    def tearDown(self):
        dds._CURRENT_TENANT_ID.reset(self.current)
        for item in reversed(self.stack):
            item.stop()
        self.tmp.cleanup()

    def test_state_and_pdf_directories_are_scoped_to_each_user(self):
        dds.write_state({'employees':[{'id':'1','name':'TESTE A'}],
                         'history':[], 'settings':{}})
        path_a=dds._state_path()
        pdf_path_a=dds._output_dir()
        token=dds._CURRENT_TENANT_ID.set(self.USER_B)
        try:
            self.assertEqual(dds.read_state()['employees'],[])
            self.assertNotEqual(dds._state_path(),path_a)
            self.assertNotEqual(dds._output_dir(),pdf_path_a)
            dds.write_state({'employees':[{'id':'2','name':'TESTE B'}],
                             'history':[], 'settings':{}})
        finally:
            dds._CURRENT_TENANT_ID.reset(token)
        self.assertEqual(dds.read_state()['employees'][0]['name'],'TESTE A')

    def test_topic_catalog_does_not_reuse_another_tenants_custom_entries(self):
        dds.init_topics_db()
        import sqlite3
        with sqlite3.connect(dds._topics_db_path()) as con:
            con.execute('''INSERT INTO topics(title,category,objective,content,source,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?)''',
                        ('Restrito à equipe A','Geral','Objetivo','Orientação privada',
                         'cadastrado pelo usuário','2026-10-23','2026-10-23'))
        token=dds._CURRENT_TENANT_ID.set(self.USER_B)
        try:
            self.assertFalse(any(row['title']=='Restrito à equipe A' for row in dds.topic_rows()))
        finally:
            dds._CURRENT_TENANT_ID.reset(token)
        self.assertTrue(any(row['title']=='Restrito à equipe A' for row in dds.topic_rows()))

    def test_rejects_invalid_tenant_directory(self):
        token=dds._CURRENT_TENANT_ID.set('../conta-de-outra-pessoa')
        try:
            with self.assertRaises(RuntimeError):
                dds._tenant_data_dir()
        finally:
            dds._CURRENT_TENANT_ID.reset(token)


if __name__=='__main__':
    unittest.main()

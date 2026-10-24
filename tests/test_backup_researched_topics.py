"""Regression tests for non-destructive DDS backup and researched-topic restoration."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as dds


class BackupResearchedTopicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_patch = patch.object(dds, 'DATA_DIR', Path(self.tmp.name))
        self.host_patch = patch.object(dds, 'HOSTED_MODE', False)
        self.data_patch.start()
        self.host_patch.start()
        dds.write_state(dds.default_state())
        dds.init_topics_db()

    def tearDown(self):
        self.host_patch.stop()
        self.data_patch.stop()
        self.tmp.cleanup()

    def add_topic(self, title, source):
        with sqlite3.connect(dds._topics_db_path()) as con:
            con.execute(
                'INSERT INTO topics(title,category,objective,content,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                (title, 'Pesquisa de segurança', 'Discutir risco', 'Orientações específicas para revisão',
                 source, '2026-10-23T19:00:00', '2026-10-23T19:00:00'))
            con.commit()

    def research_source(self):
        return 'WEB-PESQUISADO:' + json.dumps([
            {'title': 'MTE', 'url': 'https://www.gov.br/trabalho-e-emprego/pt-br/assuntos',
             'excerpt': 'Referência técnica para o assunto.'}
        ])

    def restore(self, backup):
        with dds.app.test_request_context('/api/restore-backup', method='POST', json=backup):
            result = dds.restore_backup_data()
            if isinstance(result, tuple):
                return result[1], result[0].get_json()
            return result.status_code, result.get_json()

    def test_backup_contains_online_topics_with_provenance(self):
        source = self.research_source()
        self.add_topic('Tema da internet', source)
        self.add_topic('Tema particular', 'cadastrado pelo usuário')
        backup = dds.backup_payload()
        self.assertEqual(len(backup['researched_topics']), 1)
        self.assertEqual(backup['researched_topics'][0]['source'], source)
        self.assertEqual(len(backup['custom_topics']), 1)
        self.assertEqual(backup['backup_version'], 1)

    def test_restore_researched_topics_without_overwriting_an_existing_edit(self):
        self.add_topic('Tema da internet', self.research_source())
        backup = dds.backup_payload()
        with sqlite3.connect(dds._topics_db_path()) as con:
            con.execute('DELETE FROM topics WHERE title=?', ('Tema da internet',))
            con.commit()
        status, body = self.restore(backup)
        self.assertEqual(status, 200, body)
        self.assertEqual(body['researched_topics'], 1)
        restored = next(row for row in dds.topic_rows() if row['title'] == 'Tema da internet')
        self.assertEqual(restored['source'], self.research_source())
        # Reimport must not overwrite a subsequent manual edit.
        with sqlite3.connect(dds._topics_db_path()) as con:
            con.execute('UPDATE topics SET content=?,source=? WHERE title=?',
                        ('Versão manual posterior', 'cadastrado pelo usuário', 'Tema da internet'))
            con.commit()
        status, body = self.restore(backup)
        self.assertEqual(status, 200, body)
        updated = next(row for row in dds.topic_rows() if row['title'] == 'Tema da internet')
        self.assertEqual(updated['content'], 'Versão manual posterior')

    def test_old_backups_without_researched_topics_remain_compatible(self):
        old_backup = dds.backup_payload()
        old_backup.pop('researched_topics')
        status, body = self.restore(old_backup)
        self.assertEqual(status, 200, body)
        self.assertEqual(body['researched_topics'], 0)

    def test_invalid_provenance_rejected_without_mutation(self):
        self.add_topic('Tema da internet', self.research_source())
        backup = dds.backup_payload()
        backup['researched_topics'][0]['source'] = 'WEB-PESQUISADO:[{"url":"http://127.0.0.1/"}]'
        status, body = self.restore(backup)
        self.assertEqual(status, 400)
        self.assertIn('Fontes', body['error'])
        self.assertTrue(any(row['title'] == 'Tema da internet' for row in dds.topic_rows()))


    def test_restoration_preserves_more_than_one_hundred_history_entries(self):
        backup = dds.backup_payload()
        backup['state']['history'] = [{'sequence': i} for i in range(125)]
        status, response = self.restore(backup)
        self.assertEqual(status, 200, response)
        self.assertEqual(len(dds.read_state()['history']), 125)
        self.assertEqual(response['history'], 125)

    def test_read_only_diagnostics_detects_missing_local_data(self):
        from flask import jsonify
        remote = dds.backup_payload()
        remote['state']['employees'] = [
            {'id': '1', 'name': 'TESTE', 'shift': 'DIURNO'}]
        with dds.app.test_request_context('/api/backup-diagnostics'):
            with patch.object(dds, '_vault_proxy',
                              return_value=jsonify({'backup': remote, 'ok': True})) as proxy:
                response = dds.dds_backup_diagnostics()
        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertTrue(result['local_has_fewer_employees'])
        self.assertEqual(result['employee_count_remote'], 1)
        self.assertEqual(result['employee_count_local'], 0)
        self.assertEqual(result['history_count_remote'], 0)
        proxy.assert_called_once_with('state')
        self.assertEqual(dds.read_state()['employees'], [])

    def test_read_only_diagnostics_compares_content_not_only_headcount(self):
        from flask import jsonify
        remote = dds.backup_payload()
        remote['state']['employees'] = [{
            'id': 'one', 'name': 'Registro original', 'shift': 'DIURNO'
        }]
        dds.write_state({'employees': [{
            'id': 'one', 'name': 'Registro modificado', 'shift': 'DIURNO'
        }], 'history': [], 'settings': dds.default_state()['settings']})
        with dds.app.test_request_context('/api/backup-diagnostics'):
            with patch.object(dds, '_vault_proxy', return_value=jsonify({'backup': remote})):
                result=dds.dds_backup_diagnostics()
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.get_json()['employee_count_local'],1)
        self.assertEqual(result.get_json()['employee_count_remote'],1)
        self.assertFalse(result.get_json()['state_matches'])

    def test_read_only_diagnostics_matches_exact_state(self):
        from flask import jsonify
        dds.write_state(dds.default_state())
        remote=dds.backup_payload()
        with dds.app.test_request_context('/api/backup-diagnostics'):
            with patch.object(dds,'_vault_proxy',return_value=jsonify({'backup':remote})):
                result=dds.dds_backup_diagnostics()
        self.assertEqual(result.status_code,200)
        self.assertTrue(result.get_json()['state_matches'])

    def test_read_only_diagnostics_rejects_corrupted_backup(self):
        from flask import jsonify
        with dds.app.test_request_context('/api/backup-diagnostics'):
            with patch.object(dds, '_vault_proxy',
                              return_value=jsonify({'backup': {'format': 'other'}})):
                result = dds.dds_backup_diagnostics()
        self.assertEqual(result[1], 503)
        self.assertFalse(result[0].get_json()['ok'])


if __name__ == '__main__':
    unittest.main()

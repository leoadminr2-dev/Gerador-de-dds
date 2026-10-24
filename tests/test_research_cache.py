"""Teste da busca de temas salvos, atualização intencional e fontes identificadas."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as dds


class SavedResearchCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_patch = patch.object(dds, 'DATA_DIR', Path(self.tmp.name))
        self.host_patch = patch.object(dds, 'HOSTED_MODE', False)
        self.data_patch.start()
        self.host_patch.start()
        dds.init_topics_db()
        self.sources = [{
            'title': 'Orientações MTE',
            'url': 'https://www.gov.br/trabalho-e-emprego/pt-br/assuntos',
            'excerpt': 'Comunicação e orientação de segurança no trabalho.',
            'authority': 'Fonte oficial'
        }]
        with sqlite3.connect(dds._topics_db_path()) as con:
            con.execute(
                """INSERT INTO topics(title,category,objective,content,source,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?)""",
                ('Comunicação clara e segura', 'Comunicação', 'Objetivo', 'Conteúdo DDS anterior',
                 'WEB-PESQUISADO:' + json.dumps(self.sources),
                 '2026-10-23T10:00:00', '2026-10-23T10:00:00')
            )
            con.commit()

    def tearDown(self):
        self.host_patch.stop()
        self.data_patch.stop()
        self.tmp.cleanup()

    def test_exact_saved_online_topic_is_fast_without_new_search(self):
        with patch.object(dds, '_web_search', side_effect=AssertionError('sem web')) as search:
            reply = dds.app.test_client().post('/api/research-topic',
                json={'title': 'comunicacao clara e segura'})
        self.assertEqual(reply.status_code, 200, reply.get_json())
        self.assertEqual(reply.get_json()['mode'], 'library')
        self.assertEqual(reply.get_json()['content'], 'Conteúdo DDS anterior')
        self.assertEqual(reply.get_json()['sources'][0]['title'], 'Orientações MTE')
        self.assertIn('históricas', reply.get_json()['message'])
        search.assert_not_called()

    def test_explicit_refresh_calls_web_and_replaces_only_online_topic(self):
        with patch.object(dds, '_web_search', return_value=self.sources) as search, \
             patch.object(dds, '_draft_from_sources', return_value='Conteúdo DDS atualizado'), \
             patch.object(dds, 'validate_content_fits_pdf'):
            reply = dds.app.test_client().post('/api/research-topic',
                json={'title':'Comunicação clara e segura','refresh_online':True})
        self.assertEqual(reply.status_code,200,reply.get_json())
        self.assertEqual(reply.get_json()['mode'],'online')
        search.assert_called_once()
        row=next(item for item in dds.topic_rows() if item['title']=='Comunicação clara e segura')
        self.assertEqual(row['content'],'Conteúdo DDS atualizado')


if __name__ == '__main__':
    unittest.main()

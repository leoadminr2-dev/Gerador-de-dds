"""Pesquisa online: resultados reais e falhas não destrutivas."""
import unittest
from unittest.mock import patch
from pathlib import Path
import ast


class ResearchContract(unittest.TestCase):
    def test_source_is_read_not_merely_snippet(self):
        source=Path(__file__).resolve().parents[1].joinpath('app.py').read_text(encoding='utf-8')
        tree=ast.parse(source)
        functions={node.name:node for node in tree.body if isinstance(node,ast.FunctionDef)}
        search=ast.get_source_segment(source,functions['_web_search'])
        endpoint=ast.get_source_segment(source,functions['research_topic'])
        self.assertIn('_fetch_text(url',search)
        self.assertIn('_extract_relevant_sentences(body,subject',search)
        self.assertIn('if not verified:',search)
        self.assertIn("'mode': 'research-unavailable'",endpoint)
        self.assertIn('503',endpoint)
        self.assertNotIn("'mode':'offline-draft'",endpoint)
        self.assertNotIn("'OFFLINE-DRAFT:'",endpoint)

    def test_search_success_and_failure_without_network(self):
        import app
        valid={'title':'Prevenção de quedas', 'url':'https://www.gov.br/trabalho-e-emprego/pt-br/exemplo',
               'snippet':'texto superficial'}
        original='A prevenção de quedas exige avaliação do piso, sinalização e procedimentos de trabalho. '*5
        with patch.object(app,'_search_searxng',return_value=[valid]),\
             patch.object(app,'_fetch_text',return_value=original):
            result=app._web_search('Prevenção de quedas')
            self.assertTrue(result)
            self.assertIn('excerpt',result[0])
            self.assertNotIn('snippet',result[0])
            self.assertIn('queda',app._draft_from_sources('Prevenção de quedas',result).lower())
        with patch.object(app,'_search_searxng',return_value=[]),\
             patch.object(app,'_search_engine',return_value=[]):
            with self.assertRaises(RuntimeError):
                app._web_search('Tema inexistente')

    def test_library_fallback_is_distinct_from_live_search(self):
        import app
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from pathlib import Path
        with TemporaryDirectory() as directory:
            previous=app.TOPICS_DB_PATH
            app.TOPICS_DB_PATH=Path(directory)/'topics.db'
            try:
                app.init_topics_db()
                with patch.object(app,'_web_search',side_effect=RuntimeError('rate limited')):
                    result=app.app.test_client().post('/api/research-topic',
                        json={'title':'Uso correto de EPI'})
                self.assertEqual(result.status_code,200)
                self.assertEqual(result.get_json()['mode'],'library')
                self.assertIn('online indisponível',result.get_json()['message'])
            finally:
                app.TOPICS_DB_PATH=previous

    def test_brave_is_optional_without_token(self):
        import app
        from unittest.mock import patch
        with patch.dict(app.os.environ,{},clear=True):
            self.assertEqual(app._brave_api_search('hierarquia'),[])

if __name__=='__main__':
    unittest.main()

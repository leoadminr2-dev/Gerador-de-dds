import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as dds


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, limit=-1):
        return json.dumps(self.payload).encode("utf-8")


class ResearchTests(unittest.TestCase):
    def test_searxng_parses_public_source(self):
        payload = {
            "results": [
                {
                    "title": "Fundacentro — segurança ocupacional",
                    "url": "https://www.gov.br/fundacentro/pt-br/centrais-de-conteudo",
                    "content": "Orientações de saúde e segurança no trabalho."
                },
                {
                    "title": "Resultado do buscador",
                    "url": "https://www.google.com/search?q=seguranca",
                    "content": "Isto não é uma fonte de conteúdo."
                }
            ]
        }
        with patch.dict(os.environ, {"DDS_SEARXNG_URL": "https://search.example"}):
            with patch.object(dds, "urlopen", return_value=FakeResponse(payload)):
                found = dds._search_searxng("segurança no trabalho")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["url"], "https://www.gov.br/fundacentro/pt-br/centrais-de-conteudo")
        self.assertIn("segurança", found[0]["snippet"])

    def test_long_unbroken_token_is_wrapped_to_the_available_width(self):
        token = "A" * 180
        lines = dds.wrap_words(token, "helv", 8.0, 70.0)
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(
            dds.fitz.get_text_length(line, fontname="helv", fontsize=8.0) <= 70.0
            for line in lines
        ))
        self.assertEqual("".join(lines), token)

    def test_content_preflight_rejects_vertical_overflow(self):
        with patch.object(dds, "TEMPLATE_PATH", Path("/path/that/does/not/exist.pdf")):
            content = "\\n".join(["RISCO E PREVENÇÃO"] * 45)
            with self.assertRaisesRegex(ValueError, "longo demais"):
                dds.validate_content_fits_pdf(content)

    def test_saving_topic_rejects_content_that_will_not_fit_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            old_db = dds.TOPICS_DB_PATH
            dds.TOPICS_DB_PATH = Path(directory) / "topics.db"
            try:
                with patch.object(dds, "TEMPLATE_PATH", Path(directory) / "missing-template.pdf"):
                    response = dds.app.test_client().post(
                        "/api/topics",
                        json={
                            "title": "Tema de teste com excesso de linhas",
                            "category": "Teste",
                            "objective": "Verificar o encaixe do texto",
                            "content": "\\n".join(["RISCO E PREVENÇÃO"] * 45),
                        },
                    )
                self.assertEqual(response.status_code, 400)
                self.assertIn("longo demais", response.get_json()["error"])
            finally:
                dds.TOPICS_DB_PATH = old_db

    def test_topic_source_metadata_keeps_only_approved_https_references(self):
        payload = [
            {"title": "MTE", "url": "https://www.gov.br/trabalho-e-emprego/"},
            {"title": "Search page", "url": "https://www.google.com/search?q=tema"},
            {"title": "Unsafe scheme", "url": "javascript:alert(1)"},
        ]
        metadata = dds.topic_source_metadata(
            "WEB-PESQUISADO:" + json.dumps(payload, ensure_ascii=False)
        )
        self.assertEqual(metadata["source_status"], "online-research")
        self.assertEqual(len(metadata["sources"]), 1)
        self.assertEqual(metadata["sources"][0]["title"], "MTE")

    def test_offline_draft_is_clear_and_airport_aware(self):
        with tempfile.TemporaryDirectory() as directory:
            previous_db = dds.TOPICS_DB_PATH
            dds.TOPICS_DB_PATH = Path(directory) / "topics.db"
            try:
                with patch.object(dds, "_web_search", side_effect=RuntimeError("busca indisponível")):
                    response = dds.app.test_client().post(
                        "/api/research-topic",
                        json={"title": "Prevenção de quedas em áreas de circulação"}
                    )
                self.assertEqual(response.status_code, 200)
                body = response.get_json()
                self.assertEqual(body["mode"], "offline-draft")
                self.assertIn("NÃO uma pesquisa verificada", body["message"])
                self.assertIn("CONTEXTO AEROPORTUÁRIO", body["content"])
                self.assertIn("passageiros", body["content"].lower())
                self.assertGreaterEqual(len(body["sources"]), 2)
            finally:
                dds.TOPICS_DB_PATH = previous_db


if __name__ == "__main__":
    unittest.main()

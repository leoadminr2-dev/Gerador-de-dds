import tempfile
import unittest
from pathlib import Path

import fitz
import app as dds


class BatchedPdfBuildTests(unittest.TestCase):
    def test_build_pdf_with_more_than_one_page_of_attendance(self):
        if not dds.TEMPLATE_PATH.exists() or not dds.LAYOUT_PATH.exists():
            self.fail("O modelo institucional e o layout de linhas precisam estar no pacote de testes.")
        employees = [
            {
                "id": f"test-{index}",
                "name": f"COLABORADOR TESTE {index:03d}",
                "shift": "DIURNO",
                "work_site": "GIG",
                "operational_unit": "GIG",
                "sector": "LIMPEZA",
            }
            for index in range(1, 111)
        ]
        payload = {
            "topic": "Teste de geração em lote com lista extensa",
            "shift": "DIURNO",
            "work_site": "GIG",
            "date": "2026-10-19",
            "content": "OBJETIVO\nValidar a geração de um diálogo diário de segurança com muitos participantes.",
        }
        original_output = dds.OUTPUT_DIR
        with tempfile.TemporaryDirectory() as directory:
            try:
                dds.OUTPUT_DIR = Path(directory)
                filename, metadata = dds.build_pdf(payload, employees)
                pdf_path = Path(directory) / filename
                self.assertTrue(pdf_path.exists())
                self.assertGreater(pdf_path.stat().st_size, 1000)
                with fitz.open(pdf_path) as document:
                    extracted = "\n".join(page.get_text() for page in document)
                    self.assertIn("COLABORADOR TESTE 001", extracted)
                    self.assertIn("COLABORADOR TESTE 110", extracted)
                    self.assertIn("PROIBIDA a reprodução não autorizada", extracted)
                    self.assertGreaterEqual(len(document), 2)
                self.assertEqual(metadata["participants"], 110)
                self.assertEqual(metadata["blank_rows"], 5)
                self.assertEqual(metadata["attendance_rows"], 115)
            finally:
                dds.OUTPUT_DIR = original_output


if __name__ == "__main__":
    unittest.main()

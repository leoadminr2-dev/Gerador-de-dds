import tempfile
import unittest
from pathlib import Path

import fitz
import app as dds


class BatchedPdfBuildTests(unittest.TestCase):
    def test_build_pdf_blank_manual_roster_without_employees(self):
        if not dds.TEMPLATE_PATH.exists() or not dds.LAYOUT_PATH.exists():
            self.fail("O modelo institucional e o layout de linhas precisam estar no pacote de testes.")
        payload = {
            "topic": "Teste DDS em branco para preenchimento manual",
            "shift": "DIURNO",
            "work_site": "GIG",
            "date": "2026-10-22",
            "content": "OBJETIVO\\nValidar a emissão de um DDS sem nomes para preenchimento manual.",
            "manual_blank": True,
        }
        original_output = dds.OUTPUT_DIR
        original_output_dir = dds._output_dir
        with tempfile.TemporaryDirectory() as directory:
            try:
                dds.OUTPUT_DIR = Path(directory)
                dds._output_dir = lambda: Path(directory)
                filename, metadata = dds.build_pdf(payload, [])
                pdf_path = Path(directory) / filename
                self.assertTrue(pdf_path.exists())
                with fitz.open(pdf_path) as document:
                    extracted = "\\n".join(page.get_text() for page in document)
                    self.assertIn("TESTE DDS EM BRANCO PARA PREENCHIMENTO MANUAL", extracted)
                    self.assertIn("PROIBIDA a reprodução não autorizada", extracted)
                    self.assertIn(f"Página 1 de {len(document)}", extracted)
                    self.assertNotIn("COLABORADOR TESTE", extracted)
                    self.assertGreaterEqual(len(document), 3)
                self.assertEqual(metadata["participants"], 0)
                self.assertTrue(metadata["manual_blank"])
                self.assertEqual(metadata["attendance_rows"], metadata["template_capacity"])
            finally:
                dds.OUTPUT_DIR = original_output
                dds._output_dir = original_output_dir

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
        original_output_dir = dds._output_dir
        with tempfile.TemporaryDirectory() as directory:
            try:
                dds.OUTPUT_DIR = Path(directory)
                # Current multi-tenant storage resolves the output directory via this helper.
                dds._output_dir = lambda: Path(directory)
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
                dds._output_dir = original_output_dir


if __name__ == "__main__":
    unittest.main()

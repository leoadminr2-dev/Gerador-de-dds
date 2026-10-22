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


    def test_responsible_names_are_unique_and_header_area_is_clean(self):
        payload = {
            "topic": "Teste visual do cabeçalho",
            "shift": "DIURNO",
            "work_site": "GIG",
            "date": "2026-10-22",
            "content": "OBJETIVO\\nValidar o cabeçalho e a limpeza dos nomes antigos.",
            "manual_blank": True,
        }
        original_output = dds.OUTPUT_DIR
        original_output_dir = dds._output_dir
        with tempfile.TemporaryDirectory() as directory:
            try:
                dds.OUTPUT_DIR = Path(directory)
                dds._output_dir = lambda: Path(directory)
                filename, _ = dds.build_pdf(payload, [])
                pdf_path = Path(directory) / filename
                with fitz.open(pdf_path) as document:
                    self.assertGreaterEqual(len(document), 3)
                    text = "\\n".join(page.get_text() for page in document)
                    self.assertEqual(text.count("01. ORLANDO BARBOSA"), 1)
                    self.assertEqual(text.count("02. EVELYN MONTOVANI"), 1)
                    self.assertIn("PROIBIDA a reprodução não autorizada", text)

                    # The narrow strip above the first generated name must be blank.
                    # It detects leftover fragments from preprinted names in the template.
                    strip = fitz.Rect(54.0, 354.5, 268.0, 362.0)
                    pix = document[0].get_pixmap(matrix=fitz.Matrix(3, 3), clip=strip, alpha=False)
                    samples = pix.samples
                    # Permit a continuous horizontal cell border, but not isolated text fragments.
                    for row in range(pix.height):
                        dark = 0
                        offset = row * pix.width * 3
                        for col in range(pix.width):
                            i = offset + col * 3
                            if min(samples[i], samples[i + 1], samples[i + 2]) < 240:
                                dark += 1
                        row_ratio = dark / max(pix.width, 1)
                        self.assertTrue(
                            row_ratio < 0.01 or row_ratio > 0.70,
                            f"Resíduo de texto na faixa dos responsáveis (linha renderizada {row}, proporção {row_ratio:.3f}).",
                        )
            finally:
                dds.OUTPUT_DIR = original_output
                dds._output_dir = original_output_dir


if __name__ == "__main__":
    unittest.main()

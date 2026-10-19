import unittest

import fitz
import app as dds


class BatchedPdfTextTests(unittest.TestCase):
    def test_centered_text_can_be_queued_and_rendered_by_textwriter(self):
        with fitz.open() as document:
            page = document.new_page()
            writer = fitz.TextWriter(page.rect)
            dds.centered_text(page, "GERADOR DE DDS", 20, 300, 100, writer=writer)
            writer.write_text(page, color=(0, 0, 0), overlay=True)
            self.assertIn("GERADOR DE DDS", page.get_text())

    def test_content_can_be_queued_and_rendered_by_textwriter(self):
        with fitz.open() as document:
            page = document.new_page()
            writer = fitz.TextWriter(page.rect)
            dds.draw_content(page, "OBJETIVO\nOrientar a equipe para trabalhar com segurança.", writer=writer)
            writer.write_text(page, color=(0, 0, 0), overlay=True)
            extracted = page.get_text()
            self.assertIn("OBJETIVO", extracted)
            self.assertIn("Orientar a equipe", extracted)


if __name__ == "__main__":
    unittest.main()

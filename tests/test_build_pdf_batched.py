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


    def test_approved_responsible_layout_applies_to_every_shift_and_site(self):
        # O padrao nao pode depender do nome: todos usam os mesmos quadradinhos
        # 01/02, entreletras, bordas e alturas aprovadas para as duas linhas.
        cases = [
            ("DIURNO", "GIG", ["ORLANDO BARBOSA", "EVELYN MONTOVANI"]),
            ("VESPERTINO", "GIG", ["KAYO LESSA", "BRUNA FRAGA"]),
            ("NOTURNO", "GIG", ["MARIA DO SOCORRO"]),
            ("DIURNO", "TECA", ["PEDRO PAULO"]),
            ("DIURNO", "UNITED", ["WILKER MARIA"]),
            ("DIURNO", "GATE GOURMET", ["MARCIA FASUTINO"]),
        ]
        original_output = dds.OUTPUT_DIR
        original_output_dir = dds._output_dir
        with tempfile.TemporaryDirectory() as directory:
            try:
                dds.OUTPUT_DIR = Path(directory)
                dds._output_dir = lambda: Path(directory)
                for shift, site, expected_names in cases:
                    with self.subTest(shift=shift, site=site):
                        payload = {
                            "topic": "Teste do padrao institucional de responsaveis",
                            "shift": shift,
                            "work_site": site,
                            "date": "2026-10-22",
                            "content": "OBJETIVO\\nValidar o quadro de responsaveis.",
                            "manual_blank": True,
                        }
                        filename, _ = dds.build_pdf(payload, [])
                        with fitz.open(Path(directory) / filename) as document:
                            words = document[0].get_text("words")
                            numbers = [
                                w for w in words
                                if 24.2 <= w[0] and w[2] <= 47.2
                                and 364.0 <= w[1] < 396.0
                                and w[4] in ("01", "02")
                            ]
                            self.assertEqual([w[4] for w in numbers],
                                             [f"{n:02d}" for n in range(1, len(expected_names) + 1)])
                            for idx, name in enumerate(expected_names):
                                # fitz reagrupa letras espaçadas em palavras selecionáveis.
                                first = name.split()[0]
                                matches = [
                                    w for w in words
                                    if w[4] == first and 53.0 <= w[0] < 269.0
                                    and 364.0 <= w[1] < 396.0
                                ]
                                self.assertEqual(len(matches), 1, name)
                                self.assertLess(abs(matches[0][1] - numbers[idx][1]), 1.5)
                                self.assertGreater(matches[0][0], 53.0)
                                untracked_width = fitz.get_text_length(first, fontname="helv", fontsize=7.0)
                                self.assertGreater(matches[0][2] - matches[0][0],
                                                   untracked_width + 0.7)
                            self.assertEqual(len(numbers), len(expected_names))
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
                    self.assertEqual(text.count("ORLANDO BARBOSA"), 1)
                    self.assertEqual(text.count("EVELYN MONTOVANI"), 1)
                    # A numeração deve aparecer na célula estreita x=24..48,
                    # sem invadir a célula do nome x=48..277.
                    words = document[0].get_text("words")
                    numbers = [
                        w for w in words
                        if 24.2 <= w[0] and w[2] <= 47.2
                        and 364.0 <= w[1] < 396.0
                    ]
                    names = [
                        w for w in words
                        if 53.0 <= w[0] < 269.0 and 350.0 <= w[1] < 396.0
                    ]
                    for number, first_name in [("01", "ORLANDO"), ("02", "EVELYN")]:
                        nums = [w for w in numbers if w[4] == number]
                        matching_names = [w for w in names if w[4] == first_name]
                        self.assertEqual(len(nums), 1, number)
                        self.assertEqual(len(matching_names), 1, first_name)
                        self.assertLess(nums[0][2], 47.2)
                        self.assertGreater(matching_names[0][0], 53.0)
                        self.assertLess(abs(nums[0][1] - matching_names[0][1]), 1.5)
                        base_width = fitz.get_text_length(first_name, fontname="helv", fontsize=7.0)
                        self.assertGreater(matching_names[0][2] - matching_names[0][0], base_width + 0.7)
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


    def test_attendance_row_grid_stays_identical_at_last_used_border(self):
        """A ultima linha mantida nao pode substituir o tracado original do PDF."""
        with fitz.open(dds.TEMPLATE_PATH) as original:
            layout = __import__("json").loads(dds.LAYOUT_PATH.read_text(encoding="utf-8"))
            for count in (3, 25):
                with self.subTest(colaboradores=count):
                    staff = [
                        {"id": str(i), "name": f"COLABORADOR TESTE {i:03d}",
                         "shift": "DIURNO", "work_site": "GIG"}
                        for i in range(1, count + 1)
                    ]
                    payload = {
                        "topic": "Auditoria das linhas de presenca",
                        "shift": "DIURNO", "work_site": "GIG",
                        "date": "2026-10-22", "content": "OBJETIVO\\nVerificar a tabela."
                    }
                    original_output = dds.OUTPUT_DIR
                    original_output_dir = dds._output_dir
                    with tempfile.TemporaryDirectory() as directory:
                        try:
                            dds.OUTPUT_DIR = Path(directory)
                            dds._output_dir = lambda: Path(directory)
                            filename, _ = dds.build_pdf(payload, staff)
                            with fitz.open(Path(directory) / filename) as generated:
                                target_count = count + dds.BLANK_ATTENDANCE_ROWS
                                for info in layout["pages"]:
                                    if target_count > len(info["rows"]):
                                        target_count -= len(info["rows"])
                                        continue
                                    if target_count == len(info["rows"]):
                                        break
                                    # Primeira linha removida: sua borda superior
                                    # e tambem borda inferior da ultima linha preservada.
                                    y = float(info["rows"][target_count]["top"])
                                    rect = fitz.Rect(22.3, y - 0.5, 569.3, y + 0.6)
                                    a = original[info["page"]].get_pixmap(
                                        matrix=fitz.Matrix(3, 3), clip=rect, alpha=False)
                                    b = generated[info["page"]].get_pixmap(
                                        matrix=fitz.Matrix(3, 3), clip=rect, alpha=False)
                                    self.assertEqual((a.width,a.height), (b.width,b.height))
                                    total = a.width * a.height
                                    different = sum(
                                        abs(a.samples[k] - b.samples[k]) > 70
                                        for k in range(0, min(len(a.samples), len(b.samples)), 3)
                                    )
                                    self.assertLess(
                                        different / max(total, 1), 0.09,
                                        "O limite da tabela foi apagado/reconstruido, "
                                        "alterando a linha institucional original."
                                    )
                                    break
                        finally:
                            dds.OUTPUT_DIR = original_output
                            dds._output_dir = original_output_dir

    def test_employee_names_numbers_and_areas_remain_inside_cells(self):
        layout = __import__("json").loads(dds.LAYOUT_PATH.read_text(encoding="utf-8"))
        staff = [
            {"id": str(i), "name": f"COLABORADOR TESTE {i:03d}",
             "shift": "DIURNO", "work_site": "GIG"}
            for i in range(1, 30)
        ]
        payload = {"topic": "Auditoria das celulas de colaboradores",
                   "shift": "DIURNO", "work_site": "GIG",
                   "date": "2026-10-22", "content": "OBJETIVO\\nVerificar os nomes."}
        original_output = dds.OUTPUT_DIR
        original_output_dir = dds._output_dir
        with tempfile.TemporaryDirectory() as directory:
            try:
                dds.OUTPUT_DIR = Path(directory)
                dds._output_dir = lambda: Path(directory)
                filename, _ = dds.build_pdf(payload, staff)
                with fitz.open(Path(directory) / filename) as generated:
                    for info in layout["pages"][:len(generated)]:
                        page = generated[info["page"]]
                        all_words = page.get_text("words")
                        for row in info["rows"]:
                            n = int(row["number"])
                            if n > len(staff):
                                break
                            top, bottom = float(row["top"]), float(row["bottom"])
                            cell_words = [w for w in all_words
                                          if top - 0.3 <= w[1] < bottom]
                            numbers = [w for w in cell_words
                                       if 22.3 <= w[0] and w[2] <= 48.6
                                       and w[4] == str(n)]
                            self.assertEqual(len(numbers), 1, f"Numero {n}")
                            name_words = [w for w in cell_words
                                          if w[4] == "COLABORADOR"]
                            self.assertEqual(len(name_words), 1, f"Nome {n}")
                            for w in name_words:
                                self.assertGreater(w[0], 48.6)
                                self.assertLess(w[2], 276.8)
                                self.assertGreater(w[1], top)
                                self.assertLess(w[3], bottom)
                            area = [w for w in cell_words if w[4] == "LIMPEZA"]
                            self.assertEqual(len(area), 1, f"Area {n}")
                            self.assertGreater(area[0][0], 276.8)
                            self.assertLess(area[0][2], 346.4)
            finally:
                dds.OUTPUT_DIR = original_output
                dds._output_dir = original_output_dir


    def test_vertical_dividers_are_uniform_and_continuous_on_all_pages(self):
        """Valida as seis linhas verticais, nao apenas um recorte pontual."""
        import json
        layout = json.loads(dds.LAYOUT_PATH.read_text(encoding="utf-8"))
        columns = layout["columns"]
        positions = [
            columns["number"][0], columns["name"][0], columns["area"][0],
            columns["unit"][0], columns["signature"][0], columns["signature"][1],
        ]
        scenarios = [
            # Primeira pagina recortada, duas paginas, pagina 3 e extensao.
            ("curto", 3, False),
            ("duas_paginas", 29, False),
            ("manual_tres_paginas", 0, True),
            ("extensao", 110, False),
        ]
        original_output, original_output_dir = dds.OUTPUT_DIR, dds._output_dir
        with tempfile.TemporaryDirectory() as directory:
            try:
                dds.OUTPUT_DIR = Path(directory)
                dds._output_dir = lambda: Path(directory)
                for label, staff_count, manual_blank in scenarios:
                    with self.subTest(scenario=label):
                        employees = [
                            {"id": f"vertical-{i}", "name": f"COLABORADOR TESTE {i:03d}",
                             "shift": "DIURNO", "work_site": "GIG"}
                            for i in range(1, staff_count + 1)
                        ]
                        payload = {
                            "topic": "Teste de colunas verticais", "shift": "DIURNO",
                            "work_site": "GIG", "date": "2026-10-22",
                            "content": "OBJETIVO\\nTestar a grade do DDS.",
                            "manual_blank": manual_blank,
                        }
                        filename, metadata = dds.build_pdf(payload, employees)
                        remaining = metadata["attendance_rows"]
                        with fitz.open(Path(directory) / filename) as generated:
                            for page_no, page in enumerate(generated):
                                available_rows = layout["pages"][0]["rows"] if page_no == 0 else layout["pages"][min(page_no, 2)]["rows"]
                                visible_count = min(remaining, len(available_rows))
                                self.assertGreater(visible_count, 0)
                                y0 = float(available_rows[0]["top"])
                                y1 = float(available_rows[visible_count - 1]["bottom"])
                                # Uma unica linha vetorial de largura idêntica
                                # para cada limite do layout, do inicio ao fim.
                                strokes = [
                                    (item[1], item[2], draw["width"])
                                    for draw in page.get_drawings()
                                    if draw["type"] == "s" and draw["width"] is not None
                                    and abs(draw["width"] - dds.ATTENDANCE_VERTICAL_WIDTH_PT) < .001
                                    for item in draw["items"] if item[0] == "l"
                                ]
                                for x in positions:
                                    found = [
                                        (p0, p1, width)
                                        for p0, p1, width in strokes
                                        if abs(p0.x - x) < .01 and abs(p1.x - x) < .01
                                        and abs(p0.y - y0) < .05
                                        and abs(p1.y - y1) < .05
                                    ]
                                    self.assertEqual(
                                        len(found), 1,
                                        f"Falta uma divisoria unica e continua x={x} na pagina {page_no+1}."
                                    )
                                    # A linha tem de ser preta e visivel em
                                    # alturas variadas (sem falhas entre celulas).
                                    for fraction in (.04, .25, .49, .76, .96):
                                        y = y0 + (y1 - y0) * fraction
                                        clip = fitz.Rect(x - .25, y - .3, x + .25, y + .3)
                                        pix = page.get_pixmap(matrix=fitz.Matrix(4, 4),
                                                              clip=clip, alpha=False)
                                        dark = sum(
                                            1 for i in range(0, len(pix.samples), 3)
                                            if min(pix.samples[i:i+3]) < 100
                                        )
                                        self.assertGreaterEqual(
                                            dark / max(1, pix.width * pix.height), .60,
                                            f"Falha visual na vertical {x} pagina {page_no+1}."
                                        )
                                remaining -= visible_count
                        self.assertEqual(remaining, 0)
            finally:
                dds.OUTPUT_DIR = original_output
                dds._output_dir = original_output_dir



if __name__ == "__main__":
    unittest.main()

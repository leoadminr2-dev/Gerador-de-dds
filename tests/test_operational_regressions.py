import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook
import app as dds


class OperationalRegressions(unittest.TestCase):
    def test_simple_shift_tab_imports_all_names(self):
        wb=Workbook()
        ws=wb.active
        ws.title='DIURNO'
        for name in ('ANA LIMA','BRUNO SILVA','CARLA SOUZA'):
            ws.append([name])
        content=io.BytesIO()
        wb.save(content)
        content.seek(0)
        staff,notes=dds.parse_xlsx(content)
        self.assertEqual([e['name'] for e in staff],
                         ['ANA LIMA','BRUNO SILVA','CARLA SOUZA'])
        self.assertTrue(all(e['shift']=='DIURNO' for e in staff))
        self.assertTrue(any('unidade' in n.lower() for n in notes))

    def test_empty_shift_tab_has_no_crash(self):
        wb=Workbook()
        wb.active.title='DIURNO'
        content=io.BytesIO()
        wb.save(content);content.seek(0)
        staff,_=dds.parse_xlsx(content)
        self.assertEqual(staff,[])

    def test_multiple_shifts_produce_one_combined_pdf(self):
        people=[
            {'id':'1','name':'ANA LIMA','shift':'DIURNO','work_site':'GIG'},
            {'id':'2','name':'BRUNA SILVA','shift':'VESPERTINO','work_site':'GIG'},
            {'id':'3','name':'CARLOS SOUZA','shift':'NOTURNO','work_site':'GIG'},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            with patch.object(dds,'_output_dir',return_value=directory), \
                 patch.object(dds,'read_state',return_value={'employees':people,'history':[],'settings':{}}), \
                 patch.object(dds,'write_state'):
                client=dds.app.test_client()
                response=client.post('/api/generate-bulk',json={
                    'topic':'Segurança operacional',
                    'content':'OBJETIVO\\nReforçar orientação de segurança.',
                    'date':'2026-10-22','shifts':['DIURNO','VESPERTINO','NOTURNO'],'units':['GIG'],
                    'employee_ids':['1','3'],
                })
                self.assertEqual(response.status_code,200,response.get_json())
                result=response.get_json()
                self.assertEqual(result['count'],1)
                self.assertEqual(result['document_count'],2)
                self.assertEqual(len(result['skipped']),1)
                self.assertTrue((directory/result['generated'][0]['metadata']['file']).exists())

    def test_manual_blank_bulk_can_generate_without_employees(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp)
            with patch.object(dds,'_output_dir',return_value=directory), \
                 patch.object(dds,'read_state',return_value={'employees':[],'history':[],'settings':{}}), \
                 patch.object(dds,'write_state'):
                response=dds.app.test_client().post('/api/generate-bulk',json={
                    'topic':'DDS em branco',
                    'content':'OBJETIVO\\nTestar preenchimento manual.',
                    'date':'2026-10-22','shifts':['DIURNO','NOTURNO'],'units':['GIG'],
                    'manual_blank':True
                })
                self.assertEqual(response.status_code,200,response.get_json())
                self.assertEqual(response.get_json()['document_count'],2)


if __name__=='__main__':
    unittest.main()

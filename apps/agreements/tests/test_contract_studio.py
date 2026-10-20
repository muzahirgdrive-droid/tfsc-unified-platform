import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import copy
import json
import tempfile
import unittest
from unittest.mock import patch
import server
import revised_content as content

def data():
    return dict(client_name='Example Practice LLC',address='100 Example Way, Las Vegas, NV 89101',
                effective_date='2026-10-05',baa_effective_date='2026-10-06',
                client_signatory='Jane Example',client_title='Managing Member',
                document_types=['baa','services'],services=[dict(id='credentialing',model='two_installments',
                rate=1000,quantity=2,installment_percent=35,milestone1='At commencement',milestone2='On submission of all applications',details='Two providers. Four payer enrollment applications per provider.')])

class Contracts(unittest.TestCase):
    def test_all_service_models_and_layout_blocks(self):
        for service in content.CATALOG:
            for model in service['models']:
                with self.subTest(service=service['id'],model=model['id']):
                    d=data(); d['services']=[dict(id=service['id'],model=model['id'],rate=5,government_rate=150,commercial_rate=175,quantity=2,hours=20,unit='completed case',details='Defined administrative scope',milestone1='Start',milestone2='Delivery')]
                    validated=server.validate(d)
                    blocks=content.build_services(validated,'SA-TEST')
                    self.assertEqual(blocks[-1]['type'],'signature')
                    self.assertTrue(any(x['type']=='table' for x in blocks))
                    text=json.dumps(blocks)
                    self.assertNotIn('--',text)
                    self.assertIn('Website Development' if service['id']=='website_development' else service['name'],text)

    def test_independent_dates_and_baa_static(self):
        d=server.validate(data()); docs=server.build_documents(d,'SA-TEST')
        self.assertIn('10/6/2026',json.dumps(docs[0]['blocks']))
        self.assertIn('October 5, 2026',json.dumps(docs[1]['blocks']))
        before=content.build_baa(d,'SA-TEST')
        d['services']=[];d['special_terms']='Changed terms';d['notice_days']=90
        self.assertEqual(before,content.build_baa(d,'SA-TEST'))

    def test_installment_amounts_and_scope_isolation(self):
        d=server.validate(data()); blocks=content.build_services(d,'SA-TEST'); text=json.dumps(blocks)
        self.assertIn('$700.00',text);self.assertIn('$1,300.00',text)
        self.assertIn('On submission of all applications',text)
        self.assertNotIn('3.1 Medical Billing',text)
        self.assertNotIn('Eligible Net Collections',text)

    def test_validation_rejects_bad_prices_dates_and_scope(self):
        for mutate in [lambda d:d.update(effective_date='2026-02-30'),lambda d:d['services'][0].update(rate='NaN'),lambda d:d['services'][0].update(rate=-1),lambda d:d.update(document_types=[]),lambda d:d['services'][0].update(model='percentage'),lambda d:d.update(services=[]),lambda d:d.update(governing_state='NV')]:
            d=data();mutate(d)
            with self.assertRaises(ValueError):server.validate(d)
        d=data();d['services']=[dict(id='medical_billing',model='percentage',rate=101)]
        with self.assertRaises(ValueError):server.validate(d)

    def test_baa_only_ignores_unselected_service_pricing(self):
        d=data();d['document_types']=['baa'];d['services']=[dict(id='invalid',rate='bad')]
        valid=server.validate(d)
        self.assertEqual(valid['services'],[])
        self.assertEqual(len(server.build_documents(valid,'SA-TEST')),1)

    def test_filename_isolation(self):
        a=server.folder_slug('../CON: Test/Practice?');b=server.folder_slug('.. CON Test Practice')
        self.assertNotIn('/',a);self.assertNotIn('\\',a);self.assertNotIn(':',a);self.assertNotEqual(a,b)
        self.assertLess(len(server.folder_slug('x'*200)),60)

    def test_custom_service(self):
        d=data();d['services']=[]
        d['custom_services']=[dict(name='Records indexing',scope='Index supplied documents.',responsibilities='Client supplies the records.',exclusions='No clinical decisions.',category='on_demand',model='one_time',rate=500)]
        valid=server.validate(d);text=json.dumps(content.build_services(valid,'SA-TEST'))
        self.assertIn('Index supplied documents.',text)

    def test_partial_export_not_published(self):
        with tempfile.TemporaryDirectory(prefix='sa-') as folder:
            d=server.validate(data());d['output_root']=str(Path(folder).resolve())
            def write(blocks,data,cid,path):Path(path).write_bytes(b'x'*1500)
            with patch.object(server,'generate_docx',side_effect=write),patch.object(server,'convert_to_pdf',side_effect=RuntimeError('Word unavailable')):
                with self.assertRaises(RuntimeError):server.generate(d)
            self.assertEqual(list(Path(folder).iterdir()),[])

if __name__=='__main__':unittest.main(verbosity=2)

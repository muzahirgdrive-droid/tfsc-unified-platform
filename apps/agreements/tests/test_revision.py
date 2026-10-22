import sys,json,unittest,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server,revised_content as content
from entities import ENTITIES
from unittest.mock import patch
from test_contract_studio import data

class Revision(unittest.TestCase):
    def test_personal_injury_pricing_and_standalone_scope(self):
        spec=next(s for s in content.CATALOG if s['id']=='personal_injury_collection')
        self.assertEqual(spec['category'],'recurring')
        self.assertEqual([m['id'] for m in spec['models']],['fte','percentage'])
        for entity in ENTITIES:
            for model,rate in [('fte',1800),('percentage',6.5)]:
                with self.subTest(entity=entity,model=model):
                    d=data();d.update(entity=entity,document_types=['services'],services=[dict(id=spec['id'],model=model,rate=rate,quantity=2,hours=40)])
                    valid=server.validate(d);blocks=content.build_services(valid,'PI-TEST')
                    text=json.dumps(blocks)
                    self.assertIn('1.1 Personal Injury Collection Services',text)
                    self.assertIn('2.2 Personal Injury Collection Services',text)
                    self.assertIn('3.2 Personal Injury Collection Services',text)
                    self.assertNotIn('1.1 Medical Billing',text)
                    if model=='percentage':
                        self.assertIn('6.5%',text)
                        self.assertIn('5.1 Eligible Net Collections',text)
                    else:
                        self.assertIn('$1,800.00',text)
                        self.assertIn('$3,600.00',text)
                        self.assertNotIn('5.1 Eligible Net Collections',text)
        for model,rate in [('pte',1000),('fixed',1000),('percentage',101),('fte',0)]:
            d=data();d['services']=[dict(id=spec['id'],model=model,rate=rate)]
            with self.assertRaises(ValueError):server.validate(d)

    def test_personal_injury_combined_fees_and_no_double_charge(self):
        d=data();d['services']=[dict(id='medical_billing',model='percentage',rate=3),dict(id='personal_injury_collection',model='percentage',rate=7)]
        blocks=content.build_services(server.validate(d),'PI-TEST')
        text=json.dumps(blocks)
        self.assertIn('1.2 Personal Injury Collection Services',text)
        self.assertIn('3.3 Personal Injury Collection Services',text)
        self.assertEqual(sum(b.get('text')=='5.1 Eligible Net Collections' for b in blocks),1)
        self.assertIn('same receipt shall not be charged a percentage fee more than once',text)
        self.assertIn('3%',text);self.assertIn('7%',text)

    def test_baa_verbatim_source(self):
        d=server.validate(data());d['effective_date']='2027-01-02'
        approved=json.loads((server.APP/'templates/baa.json').read_text(encoding='utf-8'))
        result=content.build_baa(d,'TEST')
        self.assertEqual(len(result),len(approved['blocks']))
        for actual,original in zip(result,approved['blocks']):
            expected=original['text'].replace('PRACTICE NAME',d['client_name']).replace('10/6/2026','1/2/2027').replace('the State of Georgia','the State of Nevada')
            self.assertEqual(actual['text'],expected)
            if actual.get('runs'):self.assertEqual(''.join(x['text'] for x in actual['runs']).strip(),expected)
        # The approved source's Georgia clause follows the governing state chosen for the package.
        self.assertNotIn('Georgia',json.dumps(result));self.assertIn('the laws of the State of Nevada, without regard',json.dumps(result))

    def test_entity_and_signature_identity(self):
        for name,entity in ENTITIES.items():
            d=server.validate(dict(data(),entity=name))
            docs=server.build_documents(d,'TEST')
            for doc in docs:
                self.assertIn(name,json.dumps(doc['blocks']))
                if name!='SCRIBE ALIGN LLC': self.assertNotIn('SCRIBE ALIGN LLC',json.dumps(doc['blocks']))
            self.assertEqual(d['provider_address'],entity['address'])
        with self.assertRaises(ValueError):server.validate(dict(data(),entity='Wrong Company'))

    def test_sections_and_service_scope_source(self):
        d=data();d['services']=[dict(id='medical_billing',model='percentage',rate=3),dict(id='credentialing',model='rate_card',government_rate=150,commercial_rate=175)]
        b=content.build_services(server.validate(d),'TEST')
        headings=[x['text'] for x in b if x['type']=='h1']
        self.assertEqual(headings[:3],['1. Services','2. Responsibilities','3. Fees'])
        texts=[x.get('text','') for x in b]
        self.assertEqual(texts.count('There is no charge for the initial set up of the services.'),1)
        source=json.loads((server.APP/'templates/service-scopes.json').read_text(encoding='utf-8'))
        for id in ['medical_billing','credentialing']:
            for clause in source[id]['scope']:
                self.assertIn(clause['text'].replace('PRACTICE NAME',d['client_name']),texts)
        self.assertIn('$175.00',json.dumps(b));self.assertIn('3%',json.dumps(b))

    def test_exact_duplicate_responsibilities_only_once(self):
        d=data();d['services']=[dict(id='medical_billing',model='percentage',rate=3),dict(id='credentialing',model='rate_card',government_rate=150,commercial_rate=150)]
        replacement=dict(content.original.SCOPES)
        for id in ['medical_billing','credentialing']:
            s=list(replacement[id]);s[1]='Shared provider obligation.';replacement[id]=tuple(s)
        with patch.dict(content.original.SCOPES,replacement):
            b=content.build_services(server.validate(d),'TEST')
        self.assertEqual(sum(x.get('text')=='Shared provider obligation.' for x in b),1)

    def test_website_types_and_separate_prices(self):
        for kind,price in [('wordpress',900),('custom',3500)]:
            d=data();d['services']=[dict(id='website_development',website_kind=kind,model='one_time',rate=price)]
            valid=server.validate(d);b=content.build_services(valid,'TEST');text=json.dumps(b)
            self.assertIn('WordPress Website' if kind=='wordpress' else 'Custom Website',text)
            self.assertIn(f'${price:,.2f}',text)
        d['services'][0]['website_kind']='unknown'
        with self.assertRaises(ValueError):server.validate(d)

if __name__=='__main__':unittest.main()

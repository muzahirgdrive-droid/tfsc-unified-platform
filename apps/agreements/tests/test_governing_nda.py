import sys,json,re,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server,revised_content as content,nda_content
from document_engine import _signature
from entities import ENTITIES
from test_contract_studio import data

EXPECTED={'SCRIBE ALIGN LLC':'Nevada','EXTENDED HEALTH SERVICES INC.':'Missouri','24-7 CONSULTANCY LLC':'Georgia'}

def text(blocks):return json.dumps(blocks)

class GoverningLaw(unittest.TestCase):
    def test_each_company_defaults_to_its_own_state_in_every_agreement(self):
        for entity,state in EXPECTED.items():
            with self.subTest(entity=entity):
                d=server.validate(dict(data(),entity=entity,document_types=['baa','services','nda']))
                self.assertEqual(d['governing_state'],state)
                docs={x['type']:x for x in server.build_documents(d,'TEST')}
                self.assertIn(f'the laws of the State of {state}, without regard',text(docs['baa']['blocks']))
                self.assertIn(f'The laws of {state}, excluding conflict',text(docs['services']['blocks']))
                self.assertIn(f'The laws of the State of {state}, excluding conflict',text(docs['nda']['blocks']))
                others=set(EXPECTED.values())-{state}
                for doc in docs.values():
                    for other in others:self.assertNotIn(other,re.sub(r'\d+ [^"]*, (Las Vegas|Hayti|Tallahassee)[^"]*','',text(doc['blocks'])).replace('Georgia law',''))

    def test_the_state_box_applies_to_baa_services_and_nda(self):
        d=server.validate(dict(data(),entity='24-7 CONSULTANCY LLC',governing_state='texas',document_types=['baa','services','nda']))
        self.assertEqual(d['governing_state'],'Texas')
        for doc in server.build_documents(d,'TEST'):
            self.assertIn('Texas',text(doc['blocks']));self.assertNotIn('Georgia',text(doc['blocks']))
        dc=server.validate(dict(data(),governing_state='District of Columbia',document_types=['baa']))
        self.assertIn('the laws of the District of Columbia, without regard',text(server.build_documents(dc,'TEST')[0]['blocks']))

    def test_source_clause_is_replaced_exactly_once(self):
        source=json.loads((server.APP/'templates/baa.json').read_text(encoding='utf-8'))
        self.assertEqual(sum(b['text'].count(content.GOVERNING_LAW) for b in source['blocks']),1)

    def test_unknown_state_rejected(self):
        with self.assertRaises(ValueError):server.validate(dict(data(),governing_state='Narnia'))

class SignatureDate(unittest.TestCase):
    def test_company_side_is_dated_with_the_effective_date_and_client_side_left_for_signing(self):
        for entity in ENTITIES:
            xml=_signature('Execution',{'entity':entity,'client_name':'Client LLC','effective_date':'2026-10-05'},'TEST')
            dates=re.findall(r'Date: ([^<]*)<',xml)
            self.assertEqual(dates[0],'October 5, 2026')
            self.assertTrue(dates[1].startswith('____'))
    def test_baa_uses_its_own_effective_date(self):
        d=server.validate(data());baa=[x for x in server.build_documents(d,'TEST') if x['type']=='baa'][0]
        self.assertEqual(baa['data']['effective_date'],'2026-10-06')
        self.assertIn('Date: October 6, 2026<',_signature('x',baa['data'],'TEST'))

class Nda(unittest.TestCase):
    def test_nda_only_package_and_options(self):
        d=server.validate(dict(data(),document_types=['nda'],nda_purpose='evaluating a credentialing project.',nda_term_years=1,nda_survival_years=5))
        self.assertEqual(d['services'],[])
        docs=server.build_documents(d,'TEST');self.assertEqual([x['type'] for x in docs],['nda'])
        self.assertEqual(docs[0]['title'],'Mutual Nondisclosure Agreement')
        blocks=docs[0]['blocks'];self.assertEqual(blocks[-1]['type'],'signature');self.assertEqual(sum(b['type']=='signature' for b in blocks),1)
        t=text(blocks)
        self.assertIn('solely for the purpose of evaluating a credentialing project (the',t)
        self.assertIn('during one (1) year from that date',t);self.assertIn('continue for five (5) years after',t)
        self.assertIn('Example Practice LLC',t);self.assertIn('SCRIBE ALIGN LLC',t);self.assertIn('protected health information',t)
        self.assertNotIn('--',t)
    def test_nda_defaults_and_bounds(self):
        d=server.validate(dict(data(),document_types=['nda']))
        self.assertEqual((d['nda_term_years'],d['nda_survival_years']),(2,3));self.assertTrue(d['nda_purpose'])
        for bad in (0,11,'x'):
            with self.assertRaises(ValueError):server.validate(dict(data(),document_types=['nda'],nda_term_years=bad))
        with self.assertRaises(ValueError):server.validate(dict(data(),document_types=['nda','other']))
    def test_nda_uses_the_selected_company_identity(self):
        for name,entity in ENTITIES.items():
            t=' '.join(b['text'] for b in nda_content.build_nda(server.validate(dict(data(),entity=name,document_types=['nda'])),'TEST'))
            self.assertIn(name,t);self.assertIn(f'("{entity["short"]}")',t)
            if name!='SCRIBE ALIGN LLC':self.assertNotIn('SCRIBE ALIGN',t)

if __name__=='__main__':unittest.main(verbosity=2)

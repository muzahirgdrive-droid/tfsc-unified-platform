"""Company details: every agreement prints the company's own address, and every company (built-in or added) is editable."""
import base64
import hashlib
import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import database as db
import entities as entmod
import esign
import server as legacy
from sqlalchemy import select
from test_contract_studio import data
from test_custom_companies import company_payload
from test_esign import Base

C247 = '24-7 CONSULTANCY LLC'
SCRIBE_ADDRESS = '9436 W. Lake Mead Blvd Ste 5 PMB 1034, Las Vegas, NV 89134'
C247_ADDRESS = '1645 Metropolitan Blvd, Tallahassee, FL 32308'


def png(colour=(29, 66, 119)) -> str:
    from PIL import Image
    out = io.BytesIO()
    Image.new('RGB', (240, 80), colour).save(out, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(out.getvalue()).decode()


def text_of(documents) -> str:
    return json.dumps(documents)


class CompanyDetails(Base):
    def tearDown(self):
        entmod.invalidate()
        super().tearDown()

    def preview(self, **over):
        r = self.post('/api/preview', dict(data(), entity=C247, output_root=str(db.OUTPUT), include_signature=False) | over)
        self.assertEqual(r.status_code, 200, r.text)
        return text_of(r.json()['documents'])

    def edit(self, key, **over):
        current = self.api.get(f'/api/admin/companies/details/{key}')
        self.assertEqual(current.status_code, 200, current.text)
        sent = ('name', 'short', 'display', 'email', 'address', 'governing_state', 'timezone', 'signatory', 'title', 'color', 'accent', 'margins')
        form = {k: v for k, v in current.json()['company'].items() if k in sent}   # exactly what the edit form sends
        return self.post(f'/api/admin/companies/details/{key}', form | over)

    def test_agreement_prints_the_company_address_even_from_a_draft_with_another_companys(self):
        # what the screenshot showed: a 24-7 agreement with a saved client's draft still holding Scribe Align's address
        self.login(self.super)
        body = self.preview(provider_address=SCRIBE_ADDRESS, provider_email='billing@scribealign.test')
        self.assertIn(C247_ADDRESS, body)
        self.assertNotIn('Lake Mead', body)
        self.assertNotIn('billing@scribealign.test', body)
        d = legacy.validate(dict(data(), entity=C247, provider_address='Anywhere else'))
        self.assertEqual((d['provider_address'], d['provider_email']), (C247_ADDRESS, entmod.get_entity(C247)['email']))

    def test_built_in_company_details_are_editable_and_used_everywhere(self):
        self.login(self.super)
        r = self.edit('24-7', address='200 New Street, Tallahassee, FL 32301', display='24-7 Consultancy LLC (FL)', short='24-7 Consultancy Group',
                      email='notices@24-7consultancyllc.com', governing_state='florida', timezone='America/New_York', color='#123456', accent='#00AEEF')
        self.assertEqual(r.status_code, 200, r.text)
        company = r.json()['company']
        self.assertEqual((company['name'], company['address'], company['governing_state'], company['color']), (C247, '200 New Street, Tallahassee, FL 32301', 'Florida', '#123456'))
        entity = entmod.get_entity(C247)
        self.assertEqual((entity['short'], entity['display'], entity['email']), ('24-7 Consultancy Group', '24-7 Consultancy LLC (FL)', 'notices@24-7consultancyllc.com'))
        self.assertEqual(entity['dark'], '#123456', 'the header colour follows a new brand colour')
        body = self.preview()
        self.assertIn('200 New Street, Tallahassee, FL 32301', body)
        self.assertIn('notices@24-7consultancyllc.com', body)
        config = {e['name']: e for e in self.api.get('/api/config').json()['entities']}
        self.assertEqual(config[C247]['address'], '200 New Street, Tallahassee, FL 32301')
        listed = {c['key']: c for c in self.api.get('/api/admin/companies').json()['companies']}
        self.assertEqual(listed['24-7']['address'], '200 New Street, Tallahassee, FL 32301')
        with db.engine.connect() as c:
            logged = c.execute(select(db.audit.c.detail).where(db.audit.c.action == 'company.updated')).scalars().all()
        self.assertTrue(any('address' in (d or {}).get('changed', []) for d in logged), logged)
        # the other built-ins are untouched
        self.assertEqual(entmod.get_entity('SCRIBE ALIGN LLC')['address'], SCRIBE_ADDRESS)

    def test_the_legal_name_stays_fixed(self):
        self.login(self.super)
        r = self.edit('24-7', name='TWENTY FOUR SEVEN LLC')
        self.assertEqual(r.status_code, 400)
        self.assertIn('legal name', r.json()['error'])
        self.assertIn(C247, list(entmod.ENTITIES))
        r = self.post('/api/admin/companies/new', company_payload())
        self.assertEqual(r.status_code, 200, r.text)
        key = r.json()['company']['key']
        self.assertEqual(self.edit(key, name='SOMETHING ELSE LLC').status_code, 400, 'renaming an added company would orphan its agreements')
        r = self.edit(key, address='1 Other Road, Austin, TX 78701')
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(entmod.get_entity('APEX CARE PARTNERS LLC')['address'], '1 Other Road, Austin, TX 78701')

    def test_only_the_super_admin_and_only_valid_values(self):
        for user in (self.admin, self.u1):
            self.login(user)
            self.assertEqual(self.api.get('/api/admin/companies/details/24-7').status_code, 403)
            self.assertEqual(self.post('/api/admin/companies/details/24-7', {'address': 'x'}).status_code, 403)
        self.login(self.super)
        for bad in ({'governing_state': 'Atlantis'}, {'email': 'not an email'}, {'color': 'blue'}, {'address': ''}, {'timezone': 'Mars/Base'}):
            with self.subTest(bad=bad):
                self.assertEqual(self.edit('24-7', **bad).status_code, 400)
        self.assertEqual(entmod.get_entity(C247)['address'], C247_ADDRESS, 'a refused change saves nothing')
        self.assertEqual(self.api.get('/api/admin/companies/details/nope').status_code, 404)

    def test_new_logo_for_a_built_in_company(self):
        self.login(self.super)
        r = self.edit('24-7', logo=png())
        self.assertEqual(r.status_code, 200, r.text)
        entity = entmod.get_entity(C247)
        self.assertTrue(entity['logo_url'].startswith('/api/companies/24-7/logo.png'))
        served = self.api.get(entity['logo_url'])
        self.assertEqual((served.status_code, served.content[:4]), (200, b'\x89PNG'))
        self.assertTrue(esign._brand_logo(entity).startswith('data:image/png;base64,'), 'signers have no sign-in, so the logo is embedded')
        import mailer
        self.assertEqual(mailer.logo_png(entity), Path(entity['logo_path']).read_bytes(), 'emails use the new logo')
        # the cached file is rebuilt from the database when it is missing (a new container)
        Path(entity['logo_path']).unlink()
        entmod.invalidate()
        self.assertTrue(Path(entmod.get_entity(C247)['logo_path']).is_file())

    def test_new_letterhead_and_margins_for_a_built_in_company(self):
        self.login(self.super)
        raw = (db.APP / 'assets' / 'letterhead-ehs.docx').read_bytes()
        r = self.edit('24-7', letterhead='data:application/octet-stream;base64,' + base64.b64encode(raw).decode(), margins={'top': 1.5})
        self.assertEqual(r.status_code, 200, r.text)
        entity = entmod.get_entity(C247)
        self.assertEqual(entity['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(hashlib.sha256(Path(entity['template_path']).read_bytes()).hexdigest(), entity['sha256'])
        self.assertEqual(entity['top'], 2160)
        self.assertIn(C247_ADDRESS, self.preview())

    def test_an_unreadable_change_never_hides_the_company(self):
        with db.engine.begin() as c:
            c.execute(db.settings.insert().values(key='company:24-7', value={'address': 'Saved St', 'letterhead_sha': 'f' * 64, 'width': 1, 'top': 1, 'bottom': 1, 'left': 1, 'right': 1, 'logo_sha': 'e' * 64}))
        entmod.invalidate()
        entity = entmod.get_entity(C247)
        self.assertEqual(entity['address'], 'Saved St')
        self.assertEqual(entity['sha256'], entmod._BUILTINS[C247]['sha256'], 'a missing letterhead file keeps the original')
        self.assertEqual(entity['logo_url'], entmod._BUILTINS[C247]['logo_url'])

    def test_without_changes_the_companies_are_exactly_as_shipped(self):
        for name, shipped in entmod._BUILTINS.items():
            self.assertEqual(entmod.get_entity(name), shipped)


if __name__ == '__main__':
    unittest.main()

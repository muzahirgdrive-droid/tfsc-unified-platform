"""Custom contracting companies, per-user service permissions, and the agreement-log report."""
import base64
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import database as db
import entities as entmod
import server as legacy
from test_esign import SCRIBE, Base

APEX = 'APEX CARE PARTNERS LLC'


def letterhead_b64() -> str:
    return 'data:application/octet-stream;base64,' + base64.b64encode((db.APP / 'assets' / 'letterhead-24-7.docx').read_bytes()).decode()


def company_payload(**over):
    payload = dict(name=APEX, short='Apex Care Partners', display='Apex Care Partners LLC',
                   address='500 Congress Ave, Austin, TX 78701', email='billing@apex.test', governing_state='Delaware',
                   timezone='America/Chicago', color='#0F7B6C', accent='#15A897', signatory='Sarah Whitman', title='Chief Executive Officer',
                   letterhead=letterhead_b64(),
                   services=[dict(name='Revenue Cycle Management', category='recurring', scope='End-to-end claims.', responsibilities='Apex staffs.', exclusions='Clinical excluded.')],
                   doc_types=['baa', 'services', 'nda'])
    payload.update(over)
    return payload


class CustomCompanies(Base):
    def tearDown(self):
        entmod.invalidate()
        super().tearDown()

    def test_only_super_admin_can_add_a_company(self):
        for user in (self.admin, self.u1):
            self.login(user)
            self.assertEqual(self.post('/api/admin/companies/new', company_payload()).status_code, 403)
            self.assertEqual(self.post('/api/admin/companies/parse-letterhead', {'letterhead': letterhead_b64()}).status_code, 403)
            self.assertEqual(self.api.get('/api/admin/companies/custom').status_code, 403)

    def test_create_company_appears_everywhere_with_its_services(self):
        self.login(self.super)
        r = self.post('/api/admin/companies/new', company_payload())
        self.assertEqual(r.status_code, 200, r.text)
        key = r.json()['company']['key']
        self.assertEqual(r.json()['company']['services'][0]['name'], 'Revenue Cycle Management')
        # It is a real entity now.
        self.assertIn(APEX, entmod.ENTITIES)
        self.assertTrue(entmod.get_entity(APEX)['custom'])
        self.assertTrue(entmod.get_entity(APEX)['template_path'].is_file())
        # It appears in the admin list and the create-agreement config.
        self.assertIn(key, [c['key'] for c in self.api.get('/api/admin/companies/custom').json()['companies']])
        config = self.api.get('/api/config').json()
        apex = next(e for e in config['entities'] if e['name'] == APEX)
        self.assertEqual(apex['governing_state'], 'Delaware')
        self.assertEqual([s['name'] for s in apex['services']], ['Revenue Cycle Management'])
        # A built-in company still carries no company-specific services (it uses the standard catalogue).
        self.assertIsNone(next(e for e in config['entities'] if e['name'] == SCRIBE)['services'])

    def test_duplicate_name_is_rejected(self):
        self.login(self.super)
        self.assertEqual(self.post('/api/admin/companies/new', company_payload()).status_code, 200)
        self.assertEqual(self.post('/api/admin/companies/new', company_payload()).status_code, 400)
        self.assertEqual(self.post('/api/admin/companies/new', company_payload(name=SCRIBE)).status_code, 400)

    def test_letterhead_must_be_a_docx(self):
        self.login(self.super)
        bad = 'data:text/plain;base64,' + base64.b64encode(b'not a docx').decode()
        self.assertEqual(self.post('/api/admin/companies/new', company_payload(letterhead=bad)).status_code, 400)

    def test_custom_company_services_drive_generation(self):
        self.login(self.super)
        key = self.post('/api/admin/companies/new', company_payload()).json()['company']['key']
        service_id = entmod.get_entity(APEX)['services'][0]['id']
        base = {'entity': APEX, 'client_name': 'Sample Clinic', 'address': '1 Main St', 'effective_date': '2026-01-01',
                'governing_state': 'Delaware', 'document_types': ['services']}
        # The company's own service validates; a standard-catalogue id does not.
        ok = legacy.validate({**base, 'services': [{'id': service_id, 'model': 'percentage', 'rate': 5}]})
        self.assertEqual(ok['services'][0]['id'], service_id)
        self.assertEqual(ok['services'][0]['scope'], 'End-to-end claims.')
        with self.assertRaises(ValueError):
            legacy.validate({**base, 'services': [{'id': 'medical_billing', 'model': 'percentage', 'rate': 5}]})
        # Removing the company hides it from the registry and config.
        self.assertEqual(self.post(f'/api/admin/companies/custom/{key}/active', {'active': False}).status_code, 200)
        entmod.invalidate()
        self.assertNotIn(APEX, entmod.ENTITIES)

    def test_per_user_service_permissions(self):
        self.login(self.super)
        # Create a user limited to two services.
        r = self.post('/api/users', {'name': 'Limited User', 'email': 'limited@scribealign.test', 'role': 'user',
                                      'companies': [SCRIBE], 'admin_id': self.admin['id'], 'services': ['medical_billing', 'ar_denials']})
        self.assertEqual(r.status_code, 200, r.text)
        limited = {'email': 'limited@scribealign.test'}
        with db.engine.begin() as c:
            c.execute(db.users.update().where(db.users.c.email == 'limited@scribealign.test').values(reset_required=False, password=self.hash))
        # Its catalogue is filtered; an unlimited user sees everything.
        self.login(self.super)
        full = self.api.get('/api/config').json()
        self.login(limited)
        limited_cfg = self.api.get('/api/config').json()
        self.assertEqual({s['id'] for s in limited_cfg['catalog']}, {'medical_billing', 'ar_denials'})
        self.assertGreater(len(full['catalog']), len(limited_cfg['catalog']))
        # Clearing the restriction (empty list) restores every service.
        self.login(self.super)
        uid = next(u['id'] for u in self.api.get('/api/users').json()['users'] if u['email'] == 'limited@scribealign.test')
        self.assertEqual(self.post(f'/api/users/{uid}', {'services': []}).status_code, 200)
        with db.engine.begin() as c:
            c.execute(db.users.update().where(db.users.c.id == uid).values(reset_required=False, password=self.hash))
        self.login(limited)
        self.assertEqual(len(self.api.get('/api/config').json()['catalog']), len(full['catalog']))

    def test_agreement_log_report_runs(self):
        self.login(self.super)
        r = self.post('/api/reports/run', {'dataset': 'agreement_log', 'filters': {'tz': 'UTC'}})
        self.assertEqual(r.status_code, 200, r.text)
        labels = [c['label'] for c in r.json()['columns']]
        for col in ('Source', 'Client / Practice', 'BAA', 'SA', 'NDA', 'PA', 'Electronic', 'Executed', 'Sent date', 'Sent by'):
            self.assertIn(col, labels)
        # It is offered in the report catalogue.
        self.assertIn('agreement_log', [d['key'] for d in self.api.get('/api/reports/catalog').json()['datasets']])


if __name__ == '__main__':
    unittest.main()

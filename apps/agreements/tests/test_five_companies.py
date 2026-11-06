"""Five more contracting companies (migration 0009), the letterhead check, the signatory guard and the standard catalogue
for companies without services of their own."""
import base64
import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import company_admin
import companies as comp
import database as db
import entities as entmod
import migrate
import server as legacy
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, select, text
from test_contract_studio import data
from test_esign import Base

ASSETS = Path(__file__).resolve().parents[1] / 'assets' / 'companies'
MANIFEST = json.loads((ASSETS / 'companies.json').read_text(encoding='utf-8'))
EXPECTED = {
    'HOUSE OF RCM, LLC': ('5900 Balcones Drive Suite 100, Austin, TX 78731', 'Texas'),
    'QUANTUM MEDICAL SERVICES': ('5904 N Division St, Spokane, WA 99208', 'Washington'),
    'MEDEQUITY SOLUTIONS, INC.': ('101 E Main Street Suite 202, Rexburg, ID 83440', 'Idaho'),
    '3E MEDICAL BILLING, LLC': ('278 West Savoy Dr., Round Lake, IL 60073', 'Illinois'),
    'BILLING NEST, LLC': ('13617 Avebury Dr, Apt 24, Laurel, MD 20708', 'Maryland'),
}
HOR = 'HOUSE OF RCM, LLC'
# the revision just before the five companies (whatever this release puts there)
BEFORE = ScriptDirectory.from_config(migrate.config()).get_revision("0009_five_companies").down_revision
HAVE_RENDERER = bool(shutil.which('soffice') or shutil.which('libreoffice')) and bool(shutil.which('pdftoppm'))


class SeedMigration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='tfsc-five-')
        self.engine = create_engine('sqlite:///' + str(Path(self.tmp.name) / 'm.db'))

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def rows(self):
        with self.engine.connect() as c:
            return {r['name']: dict(r) for r in c.execute(text('SELECT * FROM companies')).mappings()}

    def test_manifest_matches_the_files_and_the_users_list(self):
        self.assertEqual({m['name'] for m in MANIFEST}, set(EXPECTED))
        for m in MANIFEST:
            with self.subTest(company=m['name']):
                self.assertEqual((m['address'], m['governing_state']), EXPECTED[m['name']])
                self.assertTrue((ASSETS / m['letterhead']).is_file() and (ASSETS / m['logo']).is_file())
                import hashlib
                self.assertEqual(hashlib.sha256((ASSETS / m['letterhead']).read_bytes()).hexdigest(), m['letterhead_sha256'])
                self.assertGreaterEqual(min(m['left'], m['right']), company_admin.MIN_SIDE_TWIPS)
                self.assertGreater(m['top'], 1440, 'the text starts below the letterhead artwork')

    def test_upgrade_adds_the_five_and_keeps_existing_data(self):
        with self.engine.begin() as c:
            command.upgrade(migrate.config(c), BEFORE)
            now = time.time()
            c.execute(text("INSERT INTO users (id, email, name, password, role, admin_id, companies, active, reset_required, created) "
                           "VALUES ('u1', 'a@b.test', 'A', 'x', 'super_admin', NULL, '[]', 1, 0, :now)"), {'now': now})
            c.execute(text("INSERT INTO settings (key, value) VALUES ('keep_me', '{\"ok\": true}')"))
        with self.engine.begin() as c:
            command.upgrade(migrate.config(c), 'head')
        rows = self.rows()
        self.assertEqual(set(rows), set(EXPECTED))
        for name, (address, state) in EXPECTED.items():
            with self.subTest(company=name):
                r = rows[name]
                self.assertEqual((r['address'], r['governing_state']), (address, state))
                self.assertEqual((r['signatory'], r['title']), ('', ''), 'a Super Admin sets who signs')
                self.assertEqual(json.loads(r['services']) if isinstance(r['services'], str) else r['services'], [], 'standard catalogue')
                self.assertTrue(r['active'])
                self.assertTrue(r['letterhead'] and r['logo'])
        with self.engine.connect() as c:
            self.assertEqual(c.execute(text("SELECT count(*) FROM settings WHERE key='keep_me'")).scalar_one(), 1)
            self.assertEqual(c.execute(text('SELECT count(*) FROM users')).scalar_one(), 1)

    def test_a_company_added_by_hand_is_left_alone_and_running_again_adds_nothing(self):
        lh = (ASSETS / MANIFEST[0]['letterhead']).read_bytes()
        with self.engine.begin() as c:
            command.upgrade(migrate.config(c), BEFORE)
            c.execute(text("INSERT INTO companies (key, name, short, display, address, email, governing_state, timezone, color, accent, dark, signatory, "
                           "title, signature_scale, letterhead, letterhead_sha256, logo, logo_ext, width, top, bottom, \"left\", \"right\", services, doc_types, "
                           "active, created, created_by, updated) VALUES ('hor_by_hand', :name, 'HoR', 'HoR', 'Their own address', '', 'Texas', "
                           "'America/Chicago', '#000000', '#111111', '#000000', 'Owner Name', 'CEO', 0.85, :lh, 'x', NULL, NULL, 9000, 1440, 1440, 1440, "
                           "1440, '[]', '[\"baa\"]', 1, 1, NULL, 1)"), {'name': HOR, 'lh': lh})
        with self.engine.begin() as c:
            command.upgrade(migrate.config(c), 'head')
        rows = self.rows()
        self.assertEqual(len(rows), 5)
        self.assertEqual((rows[HOR]['key'], rows[HOR]['address'], rows[HOR]['signatory']), ('hor_by_hand', 'Their own address', 'Owner Name'))
        # an update that is stepped back and run again (agreements.sh rollback, then update) adds nothing twice
        with self.engine.begin() as c:
            c.execute(text('UPDATE alembic_version SET version_num=:v'), {'v': BEFORE})
        with self.engine.begin() as c:
            command.upgrade(migrate.config(c), 'head')
        self.assertEqual(len(self.rows()), 5)

    def test_downgrade_keeps_a_company_that_has_agreements(self):
        with self.engine.begin() as c:
            command.upgrade(migrate.config(c), 'head')
            c.execute(text("INSERT INTO users (id, email, name, password, role, admin_id, companies, active, reset_required, created) "
                           "VALUES ('u1', 'a@b.test', 'A', 'x', 'user', NULL, '[]', 1, 0, 1)"))
            c.execute(text("INSERT INTO clients (id, owner_id, name, name_key, address, draft, updated) VALUES ('c1', 'u1', 'Clinic', 'k', '', '{}', 1)"))
            c.execute(text("INSERT INTO contracts (id, client_id, owner_id, entity, created, payload, folder, version, legacy) "
                           "VALUES ('Q-1', 'c1', 'u1', 'QUANTUM MEDICAL SERVICES', 1, '{}', '/x', '5', 0)"))
        with self.engine.begin() as c:
            command.downgrade(migrate.config(c), BEFORE)
        self.assertEqual(set(self.rows()), {'QUANTUM MEDICAL SERVICES'})


class FiveCompaniesInTheApp(Base):
    def tearDown(self):
        entmod.invalidate()
        super().tearDown()

    def post(self, path, body=None, client=None, **headers):
        return (client or self.api).post(path, json=body if body is not None else {}, headers={'X-CSRF-Token': self.csrf, **headers})

    def payload(self, **over):
        p = dict(data(), entity=HOR, governing_state='Texas', output_root=str(db.OUTPUT), include_signature=False,
                 services=[dict(id='medical_billing', model='percentage', rate=5)])
        p.update(over)
        return p

    def test_they_are_companies_everywhere_with_the_standard_catalogue(self):
        entmod.invalidate()
        for name, (address, state) in EXPECTED.items():
            e = entmod.get_entity(name)
            self.assertEqual((e['address'], e['governing_state']), (address, state))
            self.assertTrue(e['custom'] and Path(e['template_path']).is_file() and Path(e['logo_path']).is_file())
        self.login(self.super)
        cfg = self.api.get('/api/config').json()
        listed = {e['name']: e for e in cfg['entities']}
        for name in EXPECTED:
            self.assertIsNone(listed[name]['services'], 'no services of its own: the builder shows the standard catalogue')
            self.assertEqual(listed[name]['signatory'], '')
        self.assertEqual(self.api.get('/api/companies/house_of_rcm/logo.png').status_code, 200)
        # a standard-catalogue service validates for them
        ok = legacy.validate({'entity': HOR, 'client_name': 'Clinic', 'address': '1 Main St', 'effective_date': '2026-01-01',
                              'governing_state': 'Texas', 'document_types': ['services'],
                              'services': [{'id': 'medical_billing', 'model': 'percentage', 'rate': 5}]})
        self.assertEqual(ok['services'][0]['id'], 'medical_billing')

    def test_generating_asks_for_the_signatory_first(self):
        self.login(self.super)
        r = self.post('/api/generate', self.payload(), **{'Idempotency-Key': db.uid()})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn('Company settings', r.json()['error'])
        self.assertIn('House of RCM', r.json()['error'])
        # the preview still works, so the letterhead can be checked before the signatory is known
        self.assertEqual(self.post('/api/preview', self.payload()).status_code, 200)
        current = self.api.get('/api/admin/companies/details/house_of_rcm').json()['company']
        sent = ('name', 'short', 'display', 'email', 'address', 'governing_state', 'timezone', 'color', 'accent', 'margins')
        form = {k: v for k, v in current.items() if k in sent} | {'signatory': 'Owner Example', 'title': 'Managing Member'}
        saved = self.post('/api/admin/companies/details/house_of_rcm', form)
        self.assertEqual(saved.status_code, 200, saved.text)
        r = self.post('/api/generate', self.payload(), **{'Idempotency-Key': db.uid()})
        self.assertEqual(r.status_code, 202, r.text)

    def test_built_in_companies_are_not_affected_by_the_guard(self):
        self.login(self.super)
        r = self.post('/api/generate', dict(data(), entity='SCRIBE ALIGN LLC', output_root=str(db.OUTPUT)), **{'Idempotency-Key': db.uid()})
        self.assertEqual(r.status_code, 202, r.text)

    def test_the_worker_reads_company_details_saved_a_moment_ago(self):
        import worker
        entmod.get_entity(HOR)   # cached in this process
        with db.engine.begin() as c:
            c.execute(db.companies.update().where(db.companies.c.key == 'house_of_rcm').values(top=3000, signatory='Owner Example', title='Member'))
        seen = {}
        real = worker.generate_docx

        def spy(blocks, data, *a, **k):
            seen['top'] = entmod.get_entity(data['entity'])['top']   # what the engine reads when it lays out the page
            return real(blocks, data, *a, **k)
        self.login(self.super)
        self.assertEqual(self.post('/api/generate', self.payload(document_types=['baa']), **{'Idempotency-Key': db.uid()}).status_code, 202)
        worker.generate_docx = spy
        try:
            job = worker.claim()
            worker.process(job)
        finally:
            worker.generate_docx = real
        self.assertEqual(seen.get('top'), 3000)


@unittest.skipUnless(HAVE_RENDERER, 'LibreOffice and pdftoppm are needed to measure a letterhead')
class LetterheadCheck(unittest.TestCase):
    def test_each_letterhead_measures_and_its_margins_clear_the_artwork(self):
        for m in MANIFEST:
            with self.subTest(company=m['name']):
                parsed = company_admin.parse_letterhead((ASSETS / m['letterhead']).read_bytes())
                fixed, check = company_admin.checked(parsed)
                self.assertTrue(check['available'], check)
                self.assertGreaterEqual(fixed['top'], round((check['header_in'] + company_admin.CLEAR_TOP_IN) * 1440) - 1)
                self.assertGreaterEqual(fixed['bottom'], round((check['footer_in'] + company_admin.CLEAR_BOTTOM_IN) * 1440) - 1)
                self.assertGreaterEqual(min(fixed['left'], fixed['right']), company_admin.MIN_SIDE_TWIPS)
                self.assertEqual((fixed['top'], fixed['bottom']), (m['top'], m['bottom']), 'the seeded margins are the measured ones')


@unittest.skipUnless(HAVE_RENDERER, 'LibreOffice and pdftoppm are needed to measure a letterhead')
class LetterheadCheckApi(Base):
    def test_parse_letterhead_reports_the_check(self):
        self.login(self.super)
        b64 = 'data:application/vnd.openxmlformats-officedocument.wordprocessingml.document;base64,' + \
            base64.b64encode((ASSETS / 'billing_nest-letterhead.docx').read_bytes()).decode()
        r = self.post('/api/admin/companies/parse-letterhead', {'letterhead': b64})
        self.assertEqual(r.status_code, 200, r.text)
        check = r.json()['check']
        self.assertTrue(check['available'])
        self.assertEqual(check['page'], 'US Letter')
        self.assertNotIn('margins', check)


class SignatoryProfile(Base):
    def test_profile_of_a_seeded_company_is_empty_until_set(self):
        with db.engine.connect() as c:
            p = comp.profile(c, HOR, with_image=False)
            self.assertEqual((p['signatory'], p['title']), ('', ''))
            self.assertIsNotNone(c.execute(select(db.companies.c.key).where(db.companies.c.name == HOR)).scalar())


class PageCounter(Base):
    """On an added company's letterhead the "Page X of Y" line sits above its footer artwork; built-ins are unchanged."""
    def footer_xml(self, name, renderer):
        import os
        import zipfile
        from unittest.mock import patch
        import server as legacy_
        from document_engine import generate_docx
        entmod.invalidate()
        with db.engine.begin() as c:
            c.execute(db.companies.update().values(signatory='Sample Signatory', title='Member'))
        d = legacy_.validate(dict(data(), entity=name, document_types=['baa']))
        with db.engine.connect() as c:
            company = comp.profile(c, name)
        legacy_.DATA_DIR = db.DATA
        doc = legacy_.build_documents(d, 'BAA-TEST-1', company)[0]
        out = self.root / (entmod.get_entity(name)['key'] + renderer + '.docx')
        with patch.dict(os.environ, {'TFSC_RENDERER': renderer}):
            generate_docx(doc['blocks'], doc['data'], 'BAA-TEST-1', out)
        with zipfile.ZipFile(out) as z:
            return ''.join(z.read(n).decode() for n in z.namelist() if n.startswith('word/footer'))

    def test_counter_clears_the_footer_artwork(self):
        e = entmod.get_entity('BILLING NEST, LLC')
        self.assertIn(f'w:after="{e["bottom"] - 1500}"', self.footer_xml('BILLING NEST, LLC', 'libreoffice'))
        page_h = 15840
        self.assertIn(f'w:y="{page_h - e["bottom"] + 150}"', self.footer_xml('BILLING NEST, LLC', 'word'))
        self.assertIn('w:after="0"', self.footer_xml('24-7 CONSULTANCY LLC', 'libreoffice'))
        self.assertIn(f'w:y="{page_h - 1500}"', self.footer_xml('24-7 CONSULTANCY LLC', 'word'))


if __name__ == '__main__':
    unittest.main()

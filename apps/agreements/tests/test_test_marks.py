"""Test agreements: the additive migration, watermarks, [TEST] emails, the client rule, marking permissions and the report exclusions."""
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import database as db
import document_engine
import esign_db as t
import insights
import marks
import migrate
import portal
import server
import worker
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from esign_support import agreement_pdf
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfReader
from sqlalchemy import create_engine, inspect, select, text
from test_contract_studio import data
from test_esign import SCRIBE, Base

RENDER = bool(shutil.which(os.environ.get('LIBREOFFICE_BIN', 'soffice')) and shutil.which('pdftoppm'))


class Migration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='tfsc-marks-')
        self.engine = create_engine('sqlite:///' + str(Path(self.tmp.name) / 'm.db'))

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def columns(self, table):
        return {c['name'] for c in inspect(self.engine).get_columns(table)}

    def test_upgrade_from_0006_keeps_every_row_real_and_downgrade_keeps_rows(self):
        with self.engine.begin() as c:
            command.upgrade(migrate.config(c), '0006_custom_companies')
        now = time.time()
        rows = [
            ('users', dict(id='u1', email='a@b.test', name='A', password='x', role='user', admin_id=None, companies='[]', active=1, reset_required=0, created=now)),
            ('clients', dict(id='c1', owner_id='u1', name='Client', name_key='k', address='', draft='{}', updated=now)),
            ('contracts', dict(id='TFSC-1', client_id='c1', owner_id='u1', entity=SCRIBE, created=now, payload='{}', folder='/x', version='4.0.0', legacy=0)),
            ('esign_envelopes', dict(id='e1', ref='AAAA-0001', owner_id='u1', entity=SCRIBE, client_id='c1', contract_id='TFSC-1', subject='S', message='',
                                     status='sent', template_name=None, settings='{}', version=1, created=now, updated=now, expiry_warned=0, completion_attempts=0)),
        ]
        with self.engine.begin() as c:
            for table, values in rows:
                c.execute(text(f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join(':' + k for k in values)})"), values)
            self.assertNotIn('test', self.columns('contracts'))
        old = db.engine
        db.engine = self.engine
        try:
            db.init_db()
        finally:
            db.engine = old
        with self.engine.begin() as c:
            self.assertEqual(c.execute(text('SELECT version_num FROM alembic_version')).scalar_one(), ScriptDirectory.from_config(migrate.config()).get_current_head())
            for table in ('clients', 'contracts', 'esign_envelopes'):
                self.assertEqual(c.execute(text(f'SELECT test FROM {table}')).scalars().all(), [0], table)
            self.assertEqual(c.execute(text("SELECT status FROM esign_envelopes WHERE id='e1'")).scalar_one(), 'sent', 'the envelope out for signature is untouched')
            self.assertEqual(compare_metadata(MigrationContext.configure(c), db.meta), [])
            # a row written by code that does not know the column (an older worker still running) reads as real
            c.execute(text("INSERT INTO clients (id, owner_id, name, name_key, address, draft, updated) VALUES ('c2', 'u1', 'Other', 'k2', '', '{}', 1)"))
            self.assertEqual(c.execute(text("SELECT test FROM clients WHERE id='c2'")).scalar_one(), 0)
        with self.engine.begin() as c:
            command.downgrade(migrate.config(c), '0006_custom_companies')
        for table in ('clients', 'contracts', 'esign_envelopes'):
            self.assertNotIn('test', self.columns(table))
        with self.engine.connect() as c:
            self.assertEqual(c.execute(text('SELECT count(*) FROM esign_envelopes')).scalar_one(), 1)
            self.assertEqual(c.execute(text('SELECT count(*) FROM clients')).scalar_one(), 2)

    def test_pre_alembic_install_gets_the_columns_from_the_migration(self):
        baseline = migrate.baseline_metadata(db.meta)
        self.assertNotIn('test', baseline.tables['clients'].c)
        self.assertNotIn('test', baseline.tables['contracts'].c)
        with self.engine.begin() as c:
            baseline.create_all(c)
            c.execute(text("INSERT INTO users (id, email, name, password, role, companies, active, reset_required, created) VALUES ('u1', 'a@b.test', 'A', 'x', 'user', '[]', 1, 0, 1)"))
            c.execute(text("INSERT INTO clients (id, owner_id, name, name_key, address, draft, updated) VALUES ('c1', 'u1', 'Client', 'k', '', '{}', 1)"))
        with self.engine.begin() as c:
            self.assertEqual(migrate.upgrade_to_head(c, db.meta), 'stamped')
            self.assertEqual(c.execute(text('SELECT test FROM clients')).scalar_one(), 0)
            self.assertEqual(compare_metadata(MigrationContext.configure(c), db.meta), [])


class Watermark(unittest.TestCase):
    def docx(self, test, entity=None):
        payload = dict(data(), test=test, **({'entity': entity} if entity else {}))
        d = server.validate(payload)
        doc = server.build_documents(d, 'TFSC-TEST')[0]
        out = Path(self.tmp.name) / f"{'test' if test else 'real'}-{doc['type']}.docx"
        document_engine.generate_docx(doc['blocks'], doc['data'], 'TFSC-TEST', out)
        return out, doc

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='tfsc-wm-')

    def tearDown(self):
        self.tmp.cleanup()

    def test_validate_accepts_the_test_key(self):
        self.assertIs(server.validate(dict(data(), test=True))['test'], True)
        self.assertIs(server.validate(dict(data(), test='yes'))['test'], False, 'only a real true marks a test')
        self.assertIs(server.validate(data())['test'], False)

    def test_every_header_of_a_test_document_carries_the_watermark(self):
        for name in portal.ENTITIES:
            with self.subTest(company=name):
                path, _ = self.docx(True, name)
                with zipfile.ZipFile(path) as z:
                    headers = [n for n in z.namelist() if re.fullmatch(r'word/header[\w-]*\.xml', n)]
                    self.assertTrue(headers)
                    for h in headers:
                        body = z.read(h).decode()
                        self.assertIn('PowerPlusWaterMarkObject', body, h)
                        self.assertIn('NOT A VALID AGREEMENT', body, h)
                real, _ = self.docx(False, name)
                with zipfile.ZipFile(real) as z:
                    self.assertFalse(any(b'PowerPlusWaterMarkObject' in z.read(n) for n in z.namelist() if n.startswith('word/header')))

    def test_a_letterhead_without_headers_gets_a_watermark_header(self):
        parts = {'word/_rels/document.xml.rels': b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>',
                 '[Content_Types].xml': b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"></Types>',
                 'word/settings.xml': b'<w:settings/>'}
        section = document_engine.add_test_watermark(parts, '<w:sectPr><w:titlePg/><w:pgSz w:w="12240" w:h="15840"/></w:sectPr>')
        self.assertIn('<w:headerReference w:type="default" r:id="rIdSATestHeader"/>', section)
        self.assertIn('<w:headerReference w:type="first" r:id="rIdSATestHeader"/>', section)
        self.assertIn(b'header-sa-test.xml', parts['word/_rels/document.xml.rels'])
        self.assertIn(b'/word/header-sa-test.xml', parts['[Content_Types].xml'])
        self.assertIn(b'NOT A VALID AGREEMENT', parts['word/header-sa-test.xml'])

    @staticmethod
    def amber_pixels(png: Path) -> int:
        image = Image.open(png).convert('RGB')
        w, h = image.size
        box = image.crop((int(w * .25), int(h * .3), int(w * .75), int(h * .7)))      # the middle of the page: never letterhead art
        raw = box.tobytes()
        return sum(1 for r, g, b in zip(raw[0::3], raw[1::3], raw[2::3]) if r > 232 and 190 < g < 236 and b < 205 and r - b > 40)

    @unittest.skipUnless(RENDER, 'LibreOffice and pdftoppm are needed to render the PDF')
    def test_generated_pdf_has_the_watermark_on_every_page(self):
        with patch.dict(os.environ, {'TFSC_RENDERER': 'libreoffice'}):
            counts = {}
            for test in (True, False):
                word, _ = self.docx(test)
                pdf = word.with_suffix('.pdf')
                document_engine.convert_to_pdf(word, pdf)
                prefix = Path(self.tmp.name) / ('t' if test else 'r')
                subprocess.run(['pdftoppm', '-r', '30', '-png', str(pdf), str(prefix)], check=True, capture_output=True)
                pages = sorted(Path(self.tmp.name).glob(prefix.name + '-*.png'))
                self.assertEqual(len(pages), len(PdfReader(pdf).pages))
                counts[test] = [self.amber_pixels(p) for p in pages]
        self.assertTrue(all(n > 40 for n in counts[True]), counts)
        self.assertTrue(all(n < 5 for n in counts[False]), counts)
        self.assertEqual(len(counts[True]), len(counts[False]), 'the watermark does not change the page layout')

    def test_pdf_watermark_marks_every_page_and_keeps_pages(self):
        raw = agreement_pdf(pages=3)
        marked = marks.watermark_pdf(raw)
        reader = PdfReader(io.BytesIO(marked))
        self.assertEqual(len(reader.pages), 3)
        for page in reader.pages:
            self.assertIn('NOT A VALID AGREEMENT', page.extract_text())


class Marks(Base):
    def generate(self, test, name='Example Practice LLC'):
        payload = dict(data(), client_name=name, output_root=str(db.OUTPUT), include_signature=False, test=test)
        r = self.api.post('/api/generate', json=payload, headers={'X-CSRF-Token': self.csrf, 'Idempotency-Key': db.uid()})
        self.assertEqual(r.status_code, 202, r.text)
        job = worker.claim()
        seen = {}

        def word(blocks, values, cid, path):
            seen['test'] = values.get('test')
            Path(path).write_bytes(b'docx-test' * 200)

        def pdf(source, path):
            Path(path).write_bytes(b'pdf-test' * 200)
        with patch.object(worker, 'generate_docx', side_effect=word), patch.object(worker, 'convert_to_pdf', side_effect=pdf):
            result = worker.process(job)
        self.assertIsNotNone(result)
        self.assertEqual(seen['test'], test, 'the document engine is told to watermark')
        return result

    def flags(self, table, key):
        with db.engine.connect() as c:
            return c.execute(select(table.c.test).where(table.c.id == key)).scalar_one()

    def test_test_generation_makes_a_test_client_and_real_work_makes_it_real(self):
        self.login(self.u1)
        first = self.generate(True)
        self.assertTrue(self.flags(db.contracts, first['contract_id']))
        self.assertTrue(self.flags(db.clients, first['id']), 'a client created by a test generation is a test client')
        listed = self.api.get('/api/clients').json()['clients']
        self.assertEqual([c['test'] for c in listed], [True])
        history = self.api.get(f"/api/clients/{first['id']}/history").json()
        self.assertTrue(history['client']['test'] and history['contracts'][0]['test'])
        self.assertTrue(self.api.get(f"/api/clients/{first['id']}/profile").json()['client']['test'])
        self.assertIs(self.api.get(f"/api/clients/{first['id']}").json()['test'], True, 'the saved draft remembers the switch')
        second = self.generate(False)
        self.assertEqual(second['id'], first['id'], 'the same client')
        self.assertFalse(self.flags(db.contracts, second['contract_id']))
        self.assertFalse(self.flags(db.clients, first['id']), 'a real generation makes it a real client')
        self.assertTrue(self.flags(db.contracts, first['contract_id']), 'the earlier test agreement stays a test')
        # an existing real client stays real when a test runs for it
        third = self.generate(True)
        self.assertFalse(self.flags(db.clients, third['id']))
        # saving a draft (no generation) with the switch on creates a test client
        r = self.post('/api/clients', dict(data(), client_name='Draft Only Clinic', test=True))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(self.flags(db.clients, r.json()['id']))
        actions = [a['code'] for a in self.api.get(f"/api/clients/{first['id']}/activity").json()['activity']]
        self.assertIn('client.marked_real', actions)

    def send_from(self, contract_id):
        r = self.post('/api/esign/envelopes', {'contract_id': contract_id})
        self.assertEqual(r.status_code, 200, r.text)
        env = r.json()
        r = self.post(f"/api/esign/envelopes/{env['id']}/send")
        self.assertEqual(r.status_code, 200, r.text)
        self.drain()
        return env

    def test_test_envelope_emails_signing_state_and_certificate(self):
        self.connect_mailbox()
        test_contract, client_id = self.contract(self.u1)
        real_contract, _ = self.contract(self.u1)
        self.login(self.u1)
        r = self.post('/api/contracts/mark-test', {'ids': [test_contract], 'test': True})
        self.assertEqual(r.status_code, 200, r.text)
        env = self.send_from(test_contract)
        self.assertTrue(env['test'], 'an envelope from a test agreement is a test envelope')
        invite = self.smtp.to('jane@client.test')[0]['msg']
        self.assertTrue(invite['Subject'].startswith('[TEST] '), invite['Subject'])
        self.assertIn('practice run', invite.get_body(preferencelist=('plain',)).get_content())
        self.assertIn('TEST ENVELOPE', invite.get_body(preferencelist=('html',)).get_content())
        jane, base, state = self.signer(self.smtp.link(self.smtp.to('jane@client.test')[0]))
        self.assertIs(state['test'], True)
        self.assertEqual(self.complete_signing(jane, base, state).status_code, 200)
        self.drain()
        notices = [m['msg']['Subject'] for m in self.smtp.to('sender@scribealign.test')]
        self.assertTrue(notices and all(s.startswith('[TEST] ') for s in notices), notices)
        cert = self.api.get(f"/api/esign/envelopes/{env['id']}/certificate")
        self.assertEqual(cert.status_code, 200)
        self.assertTrue(all('NOT A VALID AGREEMENT' in p.extract_text() for p in PdfReader(io.BytesIO(cert.content)).pages), 'the certificate is marked TEST')
        with db.engine.connect() as c:
            finals = c.execute(select(t.documents.c.final_pdf).where(t.documents.c.envelope_id == env['id'])).scalars().all()
        self.assertTrue(finals)
        # documents generated from a test agreement already carry the watermark from Word, so they are not marked twice
        self.assertFalse(any('NOT A VALID AGREEMENT' in PdfReader(io.BytesIO(f)).pages[0].extract_text() for f in finals))
        self.assertTrue(self.flags(db.clients, client_id), 'a client with only test work is a test client')
        self.smtp.messages.clear()
        real = self.send_from(real_contract)
        self.assertFalse(real['test'])
        self.assertFalse(self.smtp.to('jane@client.test')[0]['msg']['Subject'].startswith('[TEST]'))
        self.assertFalse(self.flags(db.clients, real['client_id']))

    def test_signed_copies_of_documents_without_a_watermark_are_watermarked(self):
        # A test envelope whose documents did not come from a test agreement (here a real one): its signed copies get the mark.
        self.connect_mailbox()
        contract_id, _ = self.contract(self.u1)
        self.login(self.u1)
        r = self.post('/api/esign/envelopes', {'contract_id': contract_id, 'test': True})
        self.assertEqual(r.status_code, 200, r.text)
        env = r.json()
        self.assertTrue(env['test'], 'the create payload can ask for a test envelope')
        self.assertEqual(self.post(f"/api/esign/envelopes/{env['id']}/send").status_code, 200)
        self.drain()
        jane, base, state = self.signer(self.smtp.link(self.smtp.to('jane@client.test')[0]))
        self.assertEqual(self.complete_signing(jane, base, state).status_code, 200)
        self.drain()
        with db.engine.connect() as c:
            finals = c.execute(select(t.documents.c.final_pdf).where(t.documents.c.envelope_id == env['id'])).scalars().all()
        self.assertTrue(finals and all('NOT A VALID AGREEMENT' in PdfReader(io.BytesIO(f)).pages[-1].extract_text() for f in finals))

    def test_marking_permissions_bulk_and_the_log(self):
        contract_id, client_id = self.contract(self.u1)
        self.login(self.u1)
        a = self.post('/api/esign/envelopes', {'contract_id': contract_id}).json()
        b = self.post('/api/esign/envelopes', {'contract_id': contract_id}).json()
        for user in (self.u2, self.ehs_user):
            self.login(user)
            self.assertEqual(self.post('/api/esign/envelopes/mark-test', {'ids': [a['id']], 'test': True}).status_code, 404)
            self.assertEqual(self.post('/api/contracts/mark-test', {'ids': [contract_id], 'test': True}).status_code, 404)
            self.assertEqual(self.post(f'/api/clients/{client_id}/test', {'test': True}).status_code, 404)
        self.assertFalse(self.flags(t.envelopes, a['id']))
        self.login(self.u1)
        self.assertEqual(self.post('/api/esign/envelopes/mark-test', {'ids': [a['id']], 'test': 'yes'}).status_code, 400)
        self.assertEqual(self.post('/api/esign/envelopes/mark-test', {'ids': [], 'test': True}).status_code, 400)
        # one envelope the person cannot edit: nothing is changed
        self.login(self.u2)
        mine = self.post('/api/esign/envelopes', {'entity': SCRIBE}).json()
        self.assertEqual(self.post('/api/esign/envelopes/mark-test', {'ids': [mine['id'], a['id']], 'test': True}).status_code, 404)
        self.assertFalse(self.flags(t.envelopes, mine['id']))
        self.login(self.admin)      # the owner's Admin may mark it
        r = self.post('/api/esign/envelopes/mark-test', {'ids': [a['id'], b['id']], 'test': True})
        self.assertEqual((r.status_code, r.json()['changed']), (200, 2), r.text)
        self.assertTrue(self.flags(db.contracts, contract_id), 'an agreement follows when all its envelopes are tests')
        detail = self.api.get(f"/api/esign/envelopes/{a['id']}").json()
        self.assertTrue(detail['test'])
        self.assertIn('Marked as a test envelope', [h['label'] for h in detail['history']])
        self.assertTrue(detail['chain_ok'], 'marking is part of the tamper-evident audit trail')
        self.login(self.super)
        r = self.post('/api/esign/envelopes/mark-test', {'ids': [a['id']], 'test': False})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(self.flags(t.envelopes, a['id']))
        self.assertTrue(self.flags(t.envelopes, b['id']))
        with db.engine.connect() as c:
            logged = c.execute(select(db.audit.c.action, db.audit.c.detail).where(db.audit.c.action.like('%.marked_%'))).all()
        actions = {x[0] for x in logged}
        self.assertTrue({'esign.marked_test', 'esign.marked_real', 'contract.marked_test'} <= actions, actions)
        self.assertTrue(all(x in insights.ACTIONS for x in actions))
        self.assertIn('agreement: ' + contract_id, insights.describe('contract.marked_test', {'contract_id': contract_id}))
        # marking the client as a test marks its agreements and envelopes; marking it real changes only the client
        self.login(self.u1)
        r = self.post(f'/api/clients/{client_id}/test', {'test': True})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(self.flags(db.clients, client_id) and self.flags(t.envelopes, a['id']))
        self.assertEqual(self.post(f'/api/clients/{client_id}/test', {'test': False}).status_code, 200)
        self.assertFalse(self.flags(db.clients, client_id))
        self.assertTrue(self.flags(t.envelopes, a['id']))

    def test_list_filter_folder_and_counts(self):
        self.login(self.u1)
        ids = [self.post('/api/esign/envelopes', {'entity': SCRIBE}).json()['id'] for _ in range(3)]
        self.assertEqual(self.post('/api/esign/envelopes/mark-test', {'ids': ids[:1], 'test': True}).status_code, 200)
        listing = lambda **q: self.api.get('/api/esign/envelopes', params=dict(box='drafts', **q)).json()  # noqa: E731
        everything = listing()
        self.assertEqual(everything['filter_counts'], {'real': 2, 'test': 1, 'all': 3})
        self.assertEqual(len(everything['envelopes']), 3)
        self.assertEqual([e['test'] for e in listing(test='test')['envelopes']], [True])
        self.assertEqual(len(listing(test='real')['envelopes']), 2)
        folder = self.api.get('/api/esign/envelopes', params={'box': 'test'}).json()
        self.assertEqual(([e['id'] for e in folder['envelopes']], folder['counts']['test']), (ids[:1], 1))
        # a draft saved from the send wizard can switch it
        env = self.api.get(f'/api/esign/envelopes/{ids[1]}').json()
        r = self.post(f'/api/esign/envelopes/{ids[1]}/draft', {'version': env['version'], 'test': True})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()['test'])
        self.assertEqual(self.api.get('/api/esign/envelopes', params={'box': 'test'}).json()['counts']['test'], 2)

    def seed_reports(self):
        """One real and one test of everything, sent and completed today."""
        now = time.time()
        with db.engine.begin() as c:
            for n, test in ((1, False), (2, True)):
                client, contract, env = f'client{n}', f'TFSC-R{n}', f'env{n}'
                c.execute(db.clients.insert().values(id=client, owner_id=self.u1['id'], name=f'Clinic {n}', name_key=f'k{n}', address='', draft={}, updated=now,
                                                     created=now, test=test))
                c.execute(db.contracts.insert().values(id=contract, client_id=client, owner_id=self.u1['id'], entity=SCRIBE, created=now, payload={}, folder='/x',
                                                       version='4.0.0', legacy=False, test=test))
                c.execute(t.envelopes.insert().values(id=env, ref=f'REF-000{n}', owner_id=self.u1['id'], entity=SCRIBE, client_id=client, contract_id=contract,
                                                      subject=f'Envelope {n}', message='', status='completed', settings={}, version=1, created=now, updated=now,
                                                      sent_at=now - 60, completed_at=now, expiry_warned=False, completion_attempts=0, test=test))
                c.execute(t.recipients.insert().values(id=f'r{n}', envelope_id=env, role='signer', role_name='Client', routing_order=1, name='Jane', email=f'j{n}@x.test',
                                                       note='', status='completed', auth_failures=0, delivery='sent', reminders_sent=0, created=now))

    def test_home_insights_and_every_report_leave_tests_out_unless_included(self):
        self.seed_reports()
        self.login(self.u1)
        home = self.api.get('/api/home').json()
        self.assertEqual((home['totals']['sent'], home['totals']['completed'], home['totals']['generated'], home['status']['completed']), (1, 1, 1, 1))
        board = self.api.get('/api/insights').json()
        self.assertEqual((board['totals']['sent'], board['totals']['generated']), (1, 1))
        self.assertEqual([x['client'] for x in board['top_clients']], ['Clinic 1'])
        for dataset in ('agreement_log', 'envelopes', 'recipients', 'agreements', 'clients'):
            with self.subTest(dataset=dataset):
                run = lambda include: self.post('/api/reports/run', {'dataset': dataset, 'filters': {'include_test': include}}).json()  # noqa: E731
                self.assertEqual(run(False)['total'], 1)
                self.assertEqual(run(True)['total'], 2)
        clients = self.post('/api/reports/run', {'dataset': 'clients', 'columns': ['client', 'envelopes', 'test'], 'filters': {'include_test': True}}).json()
        self.assertEqual(sorted(r[0] for r in clients['rows']), ['Clinic 1', 'Clinic 2'])
        self.assertIn('✓', [r[2] for r in clients['rows']], 'the Test column shows which rows are tests')
        self.login(self.super)
        users = self.post('/api/reports/run', {'dataset': 'users', 'columns': ['name', 'agreements', 'envelopes'], 'filters': {'owner': self.u1['id']}}).json()
        self.assertEqual(users['rows'][0][1:], [1, 1], 'user totals count real work only')
        self.login(self.u1)
        export = self.post('/api/reports/export', {'dataset': 'envelopes', 'format': 'xlsx', 'filters': {}})
        self.assertEqual(export.status_code, 200)
        from openpyxl import load_workbook
        book = load_workbook(io.BytesIO(export.content))
        self.assertEqual(book['Envelopes'].max_row, 2, 'header and the one real envelope')
        self.assertIn(('Test agreements', 'Left out'), [(r[0].value, r[1].value) for r in book['Report details'].iter_rows()])
        legacy = self.api.get('/api/esign/reports', params={'since': 0, 'until': time.time() + 60}).json()
        self.assertEqual(legacy['sent'], 1)
        saved = self.post('/api/reports/saved', {'name': 'With tests', 'definition': {'dataset': 'envelopes', 'filters': {'include_test': True}}})
        self.assertEqual(saved.status_code, 200)
        catalog = self.api.get('/api/reports/catalog').json()
        self.assertTrue(next(r for r in catalog['saved'] if r['name'] == 'With tests')['definition']['filters']['include_test'])

    def test_static_stylesheet_is_public_and_linked(self):
        api = TestClient(portal.app, base_url='http://127.0.0.1:8765')
        self.assertEqual(api.get('/marks.css').status_code, 200)
        self.assertIn('<link rel="stylesheet" href="/marks.css">', (db.APP / 'index.html').read_text())
        api.close()


if __name__ == '__main__':
    unittest.main()

"""Client records: permanent client numbers, past signed agreements, the clients register, the Signed BAA register and the
import of previous clients (the additive migration, every creation path, detection from PDFs, access control)."""
import base64
import csv
import io
import json
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import client_numbers
import clients
import database as db
import esign_db as t
import migrate
import migrate_legacy
import pdf_facts
import portal
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from entities import ENTITIES
from esign_support import agreement_pdf
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas
from sqlalchemy import create_engine, func, select, text, update
from test_contract_studio import data
from test_esign import EHS, SCRIBE, Base

CONSULTANCY = '24-7 CONSULTANCY LLC'
ENVELOPE_ID = '7F3A2C1E-9B04-4E1D-8F2A-1234567890AB'


def pdf_with(lines_by_page, image_only=False) -> bytes:
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=LETTER)
    for lines in lines_by_page:
        if image_only:
            pdf.rect(60, 400, 300, 200, fill=1)
        for i, line in enumerate(lines):
            pdf.drawString(60, 740 - 18 * i, line)
        pdf.showPage()
    pdf.save()
    return buf.getvalue()


def docusign_baa(client='Bluebonnet Behavioral Health, PLLC', company='24-7 CONSULTANCY LLC', envelope=ENVELOPE_ID) -> bytes:
    return pdf_with([[f'Docusign Envelope ID: {envelope}', 'BUSINESS ASSOCIATE AGREEMENT',
                      f'This Business Associate Agreement is entered into by {company} and {client}.', 'Effective Date: March 1, 2022', '',
                      'Covered Entity', 'Name: Dana Whitfield', 'Title: Owner', 'Date: 3/4/2022', '', f'Business Associate: {company}',
                      'Name: Danish Ather', 'Title: Chief Operating Officer'],
                     ['Certificate Of Completion', f'Envelope Id: {envelope.replace("-", "")} Status: Completed', 'Signer Events Signature Timestamp',
                      'Dana Whitfield', 'dana@bluebonnet.test', 'Sent: 3/2/2022 | 09:12 AM', 'Signed: 3/4/2022 | 10:15 AM',
                      'Envelope Summary Events Status Timestamps', 'Completed Security Checked 3/4/2022 | 10:16 AM']])


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


# ------------------------------------------------------------------ the migration
class Migration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='tfsc-records-')
        self.engine = create_engine('sqlite:///' + str(Path(self.tmp.name) / 'm.db'))

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def insert(self, c, table, **values):
        c.execute(text(f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join(':' + k for k in values)})"), values)

    def upgrade(self):
        old = db.engine
        db.engine = self.engine
        try:
            db.init_db()
        finally:
            db.engine = old

    def numbers(self, c):
        return dict(c.execute(text('SELECT id, client_number FROM clients')).all())

    def test_backfill_order_idempotency_and_rerun_after_a_rollback(self):
        with self.engine.begin() as c:
            command.upgrade(migrate.config(c), '0007_test_marks')
        now = time.time()
        with self.engine.begin() as c:
            self.insert(c, 'users', id='u1', email='a@b.test', name='A', password='x', role='user', admin_id=None, companies='[]', active=1, reset_required=0, created=now)
            # created first, but updated last: the creation time decides
            self.insert(c, 'clients', id='c-old', owner_id='u1', name='Oldest', name_key='k1', address='', draft='{}', updated=now, created=now - 300)
            self.insert(c, 'clients', id='c-legacy', owner_id='u1', name='No creation time', name_key='k2', address='', draft='{}', updated=now - 200, created=None)
            self.insert(c, 'clients', id='c-new', owner_id='u1', name='Newest', name_key='k3', address='', draft='{}', updated=now - 500, created=now - 100)
            self.insert(c, 'clients', id='c-tie-b', owner_id='u1', name='Tie B', name_key='k4', address='', draft='{}', updated=now, created=now - 50)
            self.insert(c, 'clients', id='c-tie-a', owner_id='u1', name='Tie A', name_key='k5', address='', draft='{}', updated=now, created=now - 50)
            self.insert(c, 'esign_envelopes', id='e1', ref='AAAA-0001', owner_id='u1', entity=SCRIBE, client_id='c-old', contract_id=None, subject='S', message='',
                        status='sent', template_name=None, settings='{}', version=1, created=now, updated=now, expiry_warned=0, completion_attempts=0)
        self.upgrade()
        with self.engine.begin() as c:
            self.assertEqual(self.numbers(c), {'c-old': 'CL-0001', 'c-legacy': 'CL-0002', 'c-new': 'CL-0003', 'c-tie-a': 'CL-0004', 'c-tie-b': 'CL-0005'})
            self.assertEqual(c.execute(text("SELECT value FROM settings WHERE key = 'client_numbers'")).scalar_one(), json.dumps({'last': 5}))
            self.assertEqual(c.execute(text("SELECT status FROM esign_envelopes WHERE id = 'e1'")).scalar_one(), 'sent', 'the envelope out for signature is untouched')
            self.assertEqual(compare_metadata(MigrationContext.configure(c), db.meta), [])
            self.assertEqual(c.execute(text('SELECT version_num FROM alembic_version')).scalar_one(), ScriptDirectory.from_config(migrate.config()).get_current_head())
            # agreements.sh rollback: the previous release runs on this schema and creates a client without a number ...
            c.execute(text("UPDATE alembic_version SET version_num = '0007_test_marks'"))
            self.insert(c, 'clients', id='c-after', owner_id='u1', name='Made during rollback', name_key='k6', address='', draft='{}', updated=now, created=now)
        # ... and the next update runs 0008 again: only the new client is numbered, after the others
        self.upgrade()
        with self.engine.begin() as c:
            numbers = self.numbers(c)
            self.assertEqual(numbers['c-after'], 'CL-0006')
            self.assertEqual(numbers['c-old'], 'CL-0001')
            self.assertEqual(len(set(numbers.values())), 6)
            self.assertEqual(compare_metadata(MigrationContext.configure(c), db.meta), [])
        self.upgrade()    # current: nothing to do
        with self.engine.begin() as c:
            self.assertEqual(len(set(self.numbers(c).values())), 6)

    def test_downgrade_and_upgrade_again(self):
        with self.engine.begin() as c:
            migrate.upgrade_to_head(c, db.meta)
            self.insert(c, 'users', id='u1', email='a@b.test', name='A', password='x', role='user', admin_id=None, companies='[]', active=1, reset_required=0, created=1)
            self.insert(c, 'clients', id='c1', owner_id='u1', name='Kept', name_key='k', address='', draft='{}', updated=1, created=1)
        with self.engine.begin() as c:
            command.downgrade(migrate.config(c), '0007_test_marks')
        with self.engine.connect() as c:
            self.assertNotIn('client_agreements', set(c.dialect.get_table_names(c)))
            self.assertEqual(c.execute(text('SELECT name FROM clients')).scalar_one(), 'Kept')
        self.upgrade()
        with self.engine.connect() as c:
            self.assertEqual(self.numbers(c), {'c1': 'CL-0001'})


# ------------------------------------------------------------------ shared data
class Records(Base):
    def client(self, owner, name, *, created=None, test=False, address='', profile=None, entity=SCRIBE):
        cid = db.uid()
        at = created or time.time()
        with db.engine.begin() as c:
            c.execute(db.clients.insert().values(id=cid, owner_id=owner['id'], name=name, name_key=clients.name_key(name), address=address,
                                                 draft={'client_name': name, 'address': address, 'entity': entity, 'id': cid}, updated=at, created=at,
                                                 profile=profile or {}, test=test))
            client_numbers.assign_one(c, cid)
        return cid

    def envelope(self, owner, client_id, status, *, kinds=('baa',), entity=SCRIBE, test=False, completed=None, signer='Jane Example'):
        eid, now = db.uid(), time.time()
        done = status == 'completed'
        body = agreement_pdf()
        with db.engine.begin() as c:
            c.execute(t.envelopes.insert().values(id=eid, ref='T' + eid[:12].upper(), owner_id=owner['id'], entity=entity, client_id=client_id, contract_id=None,
                                                  subject='Agreements', message='', status=status, settings={}, version=1, created=now, updated=now, sent_at=now - 60,
                                                  completed_at=(completed or now) if done else None, expiry_warned=False, completion_attempts=0, test=test,
                                                  certificate=b'%PDF-certificate' if done else None, certificate_sha256='c' * 64 if done else None))
            for i, kind in enumerate(kinds):
                c.execute(t.documents.insert().values(id=db.uid(), envelope_id=eid, position=i, name=f'{kind.upper()}.pdf', source='generated', agreement=kind,
                                                      pdf=body, sha256='a' * 64, pages=2, page_sizes=[[612, 792, 0]] * 2, created=now,
                                                      final_pdf=body if done else None, final_sha256='b' * 64 if done else None))
            c.execute(t.recipients.insert().values(id=db.uid(), envelope_id=eid, role='signer', role_name='Client', routing_order=1, name=signer, email='j@x.test',
                                                   note='', status='completed' if done else 'sent', auth_failures=0, delivery='sent', reminders_sent=0, created=now))
        return eid

    def past(self, client_id, entity=SCRIBE, kind='baa', source='docusign', signed_on='2022-03-04', **extra):
        r = self.post(f'/api/clients/{client_id}/past-agreements', dict(entity=entity, kind=kind, source=source, signed_on=signed_on, **extra))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def upload(self, client_id, raw, name='Signed BAA.pdf'):
        r = self.post(f'/api/clients/{client_id}/files', {'name': name, 'data': b64(raw), 'category': 'contract'})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['id']

    def register(self, **q):
        r = self.api.get('/api/client-records/register', params=q)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()


# ------------------------------------------------------------------ client numbers
class Numbers(Records):
    def all_numbers(self):
        with db.engine.connect() as c:
            return dict(c.execute(select(db.clients.c.name, db.clients.c.client_number)).all())

    def test_every_creation_path_numbers_the_client(self):
        self.login(self.u1)
        r = self.post('/api/clients/new', {'name': 'New Form Clinic', 'entity': SCRIBE})
        self.assertEqual(r.json()['client_number'], 'CL-0001')
        self.assertEqual(self.post('/api/clients', dict(data(), client_name='Draft Clinic', entity=SCRIBE)).status_code, 200)     # agreement form / generation
        with db.engine.begin() as c:     # bulk send
            clients.ensure_client(c, dict(self.u1), 'Bulk Clinic', SCRIBE)
        with db.engine.begin() as c:     # the CRM bridge saves its drafts the same way
            portal.save_draft(c, dict(self.u1), dict(data(), client_name='CRM Clinic', entity=SCRIBE, id=''))
        (db.DATA / 'clients').mkdir(exist_ok=True)
        (db.DATA / 'clients' / 'old.json').write_text(json.dumps(dict(data(), client_name='Legacy Clinic', entity=SCRIBE)))
        self.assertEqual(migrate_legacy.import_existing(dict(self.super))['clients'], 1)
        numbers = self.all_numbers()
        self.assertEqual([numbers[n] for n in ('New Form Clinic', 'Draft Clinic', 'Bulk Clinic', 'CRM Clinic', 'Legacy Clinic')],
                         ['CL-0001', 'CL-0002', 'CL-0003', 'CL-0004', 'CL-0005'])
        self.assertEqual(len(set(numbers.values())), len(numbers))
        profile = self.api.get(f"/api/clients/{r.json()['id']}/profile").json()
        self.assertEqual(profile['client']['client_number'], 'CL-0001')

    def test_safety_net_search_and_numbers_never_reused(self):
        # a client written without a number (a test helper, or an older release after a rollback) is numbered before any list
        self.contract(self.u1)
        self.login(self.u1)
        listed = self.api.get('/api/clients').json()['clients']
        self.assertEqual([c['client_number'] for c in listed], ['CL-0001'])
        for name in ('Second Clinic', 'Third Clinic'):
            self.post('/api/clients/new', {'name': name, 'entity': SCRIBE})
        for q in ('CL-0003', 'cl-3', '3', '0003'):
            self.assertEqual([c['client_name'] for c in self.api.get('/api/clients', params={'q': q}).json()['clients']], ['Third Clinic'], q)
        self.assertEqual([c['name'] for c in self.register(q='CL-0002')['clients']], ['Second Clinic'])
        # the highest number is removed: the next client still gets a new number
        with db.engine.begin() as c:
            third = c.execute(select(db.clients.c.id).where(db.clients.c.client_number == 'CL-0003')).scalar_one()
            c.execute(db.audit.delete().where(db.audit.c.client_id == third))
            c.execute(db.clients.delete().where(db.clients.c.id == third))
        self.assertEqual(self.post('/api/clients/new', {'name': 'Fourth Clinic', 'entity': SCRIBE}).json()['client_number'], 'CL-0004')

    def test_number_format_and_order(self):
        self.assertEqual(client_numbers.format_number(7), 'CL-0007')
        self.assertEqual(client_numbers.format_number(10000), 'CL-10000')
        with db.engine.begin() as c:
            # a client that already holds CL-9999 (written directly, as an import from another system might) and one without a number
            a, b = db.uid(), db.uid()
            c.execute(db.clients.insert().values(id=a, owner_id=self.u1['id'], name='A', name_key='ka', address='', draft={}, updated=1, client_number='CL-9999'))
            c.execute(db.clients.insert().values(id=b, owner_id=self.u1['id'], name='B', name_key='kb', address='', draft={}, updated=2))
            client_numbers.ensure_client_numbers(c)
            self.assertEqual(c.execute(select(db.clients.c.client_number).where(db.clients.c.id == b)).scalar(), 'CL-10000')
            self.assertEqual(client_numbers.assign_one(c, b), 'CL-10000', 'a client keeps its number')
        with db.engine.begin() as c:
            third = db.uid()
            c.execute(db.clients.insert().values(id=third, owner_id=self.u1['id'], name='C', name_key='kc', address='', draft={}, updated=1))
            self.assertEqual(client_numbers.assign_one(c, third), 'CL-10001', 'CL-10000 sorts after CL-9999')

    def test_numbers_in_reports(self):
        self.login(self.u1)
        cid = self.post('/api/clients/new', {'name': 'Report Clinic', 'entity': SCRIBE}).json()['id']
        run = self.post('/api/reports/run', {'dataset': 'clients', 'filters': {}}).json()
        self.assertEqual(run['columns'][0]['key'], 'client_no')
        self.assertEqual(run['rows'][0][:2], ['CL-0001', 'Report Clinic'])
        export = self.post('/api/reports/export', {'dataset': 'clients', 'format': 'csv', 'filters': {}})
        rows = list(csv.reader(io.StringIO(export.content.decode('utf-8-sig'))))
        self.assertEqual(rows[0][:2], ['Client no.', 'Client'])
        self.assertEqual(rows[1][:2], ['CL-0001', 'Report Clinic'])
        self.assertTrue(cid)


# ------------------------------------------------------------------ detection
class Detection(Records):
    def test_docusign_pdf(self):
        found = pdf_facts.read(docusign_baa(), list(ENTITIES.values()), {'Danish Ather'})
        self.assertEqual((found['source'], found['reference'], found['entity'], found['kind']), ('docusign', ENVELOPE_ID, CONSULTANCY, 'baa'))
        self.assertEqual((found['signed_on'], found['effective_on'], found['client_signer'], found['client_title']), ('2022-03-04', '2022-03-01', 'Dana Whitfield', 'Owner'))
        self.assertNotIn('excerpt', pdf_facts.public(found))

    def test_jotform_manual_and_scanned(self):
        jot = pdf_facts.read(pdf_with([['Jotform', 'Submission ID: 5719320123456', 'Mutual Nondisclosure Agreement', 'Scribe Align LLC and Coastal Wound Care PLLC',
                                        'Signature', 'Signed on: February 14, 2023']]), list(ENTITIES.values()), set())
        self.assertEqual((jot['source'], jot['reference'], jot['kind'], jot['entity'], jot['signed_on']), ('jotform', '5719320123456', 'nda', SCRIBE, '2023-02-14'))
        manual = pdf_facts.read(pdf_with([['SERVICES AGREEMENT', 'between Extended Health Services Inc. and Riverbend Family Clinic', 'Date: 01/18/2024']]),
                                list(ENTITIES.values()), set())
        self.assertEqual((manual['source'], manual['kind'], manual['entity'], manual['signed_on']), ('', 'services', EHS, '2024-01-18'))
        scanned = pdf_facts.read(pdf_with([[]], image_only=True), list(ENTITIES.values()), set(), 'Desert Rose BAA.pdf')
        self.assertEqual((scanned['text'], scanned['source'], scanned['kind'], scanned['signed_on']), (False, '', 'baa', ''), 'the file name says BAA')

    def test_read_endpoint(self):
        cid = self.client(self.u1, 'Bluebonnet Behavioral Health, PLLC')
        self.login(self.u1)
        fid = self.upload(cid, docusign_baa(company='SCRIBE ALIGN LLC'))
        r = self.post(f'/api/clients/{cid}/past-agreements/read', {'file_id': fid})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['found']['entity'], SCRIBE)
        self.assertEqual(r.json()['file']['pages'], 2)
        self.login(self.u2)
        self.assertEqual(self.post(f'/api/clients/{cid}/past-agreements/read', {'file_id': fid}).status_code, 404)


# ------------------------------------------------------------------ past agreements
class PastAgreements(Records):
    def test_create_update_delete_permissions_and_audit(self):
        cid = self.client(self.u1, 'Riverbend Family Clinic')
        self.login(self.u1)
        fid = self.upload(cid, docusign_baa(company='SCRIBE ALIGN LLC'))
        made = self.past(cid, signed_on='2022-03-04', effective_on='2022-03-01', client_signer='Dana Whitfield · Owner', reference=ENVELOPE_ID, file_id=fid)
        self.assertEqual((made['source'], made['kind'], made['title']), ('docusign', 'baa', 'Business Associate Agreement'))
        listing = self.api.get(f'/api/clients/{cid}/agreements').json()
        past = [x for x in listing['items'] if x['origin'] == 'past']
        self.assertEqual(len(past), 1)
        self.assertEqual(past[0]['file']['url'], f'/api/clients/files/{fid}')
        panel = {c['name']: c for c in listing['companies']}
        self.assertEqual(panel[SCRIBE]['baa'], {'date': '2022-03-04', 'source': 'docusign'})
        # invalid values
        for bad in ({'entity': 'NOT A COMPANY'}, {'kind': 'lease'}, {'source': 'fax'}, {'signed_on': '03/04/2022'}, {'signed_on': '2999-01-01'}):
            self.assertEqual(self.post(f'/api/clients/{cid}/past-agreements', dict({'entity': SCRIBE, 'kind': 'baa', 'source': 'manual'}, **bad)).status_code, 400, bad)
        self.assertEqual(self.post(f'/api/clients/{cid}/past-agreements', {'entity': EHS, 'kind': 'baa', 'source': 'manual'}).status_code, 403, 'not their company')
        # the signed copy cannot be removed while the agreement uses it
        self.assertEqual(self.post(f'/api/clients/files/{fid}/delete').status_code, 409)
        library = self.api.get(f'/api/clients/{cid}/documents').json()['documents']
        self.assertEqual([d['group'] for d in library if d['id'] == fid], ['Signed before this portal'])
        # other people
        self.login(self.u2)
        self.assertEqual(self.post(f"/api/clients/{cid}/past-agreements/{made['id']}", {'source': 'manual'}).status_code, 404)
        self.assertEqual(self.api.get(f'/api/clients/{cid}/agreements').status_code, 404)
        self.login(self.ehs_user)
        self.assertEqual(self.post(f'/api/clients/{cid}/past-agreements', {'entity': EHS, 'kind': 'baa', 'source': 'manual'}).status_code, 404)
        self.login(self.admin)
        r = self.post(f"/api/clients/{cid}/past-agreements/{made['id']}", {'source': 'manual', 'kind': 'services'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()['source'], r.json()['title']), ('manual', 'Services Agreement'))
        self.assertEqual(self.post(f"/api/clients/{cid}/past-agreements/{made['id']}", {'delete': True}).status_code, 200)
        self.assertEqual(self.post(f'/api/clients/files/{fid}/delete').status_code, 200, 'the file can go once the agreement is removed')
        with db.engine.connect() as c:
            actions = c.execute(select(db.audit.c.action).where(db.audit.c.client_id == cid, db.audit.c.action.like('client.past_agreement_%'))
                                .order_by(db.audit.c.created)).scalars().all()
        self.assertEqual(actions, ['client.past_agreement_added', 'client.past_agreement_updated', 'client.past_agreement_removed'])
        self.login(self.super)
        log = self.api.get('/api/activity', params={'category': 'clients'}).json()
        self.assertIn('Added a past signed agreement', [e['action'] for e in log['entries']])


# ------------------------------------------------------------------ the clients register
class Register(Records):
    def seed(self):
        self.portal_client = self.client(self.u1, 'Portal Signed Clinic', address='12 Oak St, Austin, TX 78704', profile={'npi': '1548293716'})
        self.envelope(self.u1, self.portal_client, 'completed', kinds=('baa', 'services'))
        self.past_client = self.client(self.u1, 'Past Signed Clinic')
        self.missing_client = self.client(self.u1, 'Missing Clinic')
        self.envelope(self.u1, self.missing_client, 'sent', kinds=('baa',))
        self.partial_client = self.client(self.u1, 'Partial Clinic')
        self.test_client = self.client(self.u1, 'Practice Clinic', test=True)
        self.login(self.u1)
        self.past(self.past_client, source='jotform', signed_on='2023-02-14')
        self.past(self.partial_client, source='manual', signed_on='2021-09-30')
        self.past(self.partial_client, kind='services', source='docusign', signed_on='2024-05-02')
        self.login(self.super)
        self.past(self.partial_client, entity=EHS, kind='services', source='manual', signed_on='2024-06-01')
        self.past(self.test_client, source='manual', signed_on='2022-01-01')
        self.login(self.u1)

    def test_aggregates(self):
        self.seed()
        d = self.register()
        self.assertEqual(d['kpis']['clients'], 4, 'test clients are left out')
        self.assertEqual((d['kpis']['covered'], d['kpis']['missing'], d['kpis']['earlier']), (3, 1, 2))
        self.assertEqual(d['kpis']['earlier_by_source'], {'docusign': 0, 'manual': 1, 'jotform': 1, 'other': 0})
        rows = {r['name']: r for r in d['clients']}
        self.assertNotIn('Practice Clinic', rows)
        p = rows['Portal Signed Clinic']
        self.assertEqual((p['baa']['source'], p['through'], p['docs']), ('portal', 'portal', {'baa': 'signed', 'services': 'signed', 'nda': 'none'}))
        self.assertEqual((p['place'], p['npi']), ('Austin, TX', '1548293716'))
        self.assertEqual((rows['Past Signed Clinic']['baa']['source'], rows['Past Signed Clinic']['baa']['date']), ('jotform', '2023-02-14'))
        m = rows['Missing Clinic']
        self.assertEqual((m['baa'], m['docs']['baa'], m['covered']), (None, 'out', False))
        # u1 only sees Scribe Align: the EHS agreement on the partial client is not theirs to see
        self.assertTrue(rows['Partial Clinic']['covered'])
        counts = self.register(baa='missing')['counts']
        self.assertEqual({k: counts[k] for k in ('all', 'signed', 'missing')}, {'all': 4, 'signed': 3, 'missing': 1})
        self.assertEqual(counts['no_baa'], 1)
        self.assertEqual([r['name'] for r in self.register(baa='missing')['clients']], ['Missing Clinic'])
        self.assertEqual(len(self.register(baa='signed')['clients']), 3)
        self.assertEqual(sorted(r['name'] for r in self.register(source='jotform')['clients']), ['Past Signed Clinic'])
        self.assertEqual(sorted(r['name'] for r in self.register(source='portal')['clients']), ['Missing Clinic', 'Portal Signed Clinic'])
        self.assertEqual([r['name'] for r in self.register(q='1548293716')['clients']], ['Portal Signed Clinic'])
        self.assertEqual([r['name'] for r in self.register(test='test')['clients']], ['Practice Clinic'])
        # the Super Admin sees the EHS agreement: the partial client lacks a BAA with EHS
        self.login(self.super)
        rows = {r['name']: r for r in self.register()['clients']}
        self.assertFalse(rows['Partial Clinic']['covered'])
        self.assertEqual(rows['Partial Clinic']['baa_missing_for'], [ENTITIES[EHS]['short']])
        self.assertEqual(rows['Partial Clinic']['baa']['date'], '2021-09-30')
        ehs = self.register(company=EHS)
        self.assertEqual([r['name'] for r in ehs['clients']], ['Partial Clinic'])
        self.assertEqual({k: ehs['counts'][k] for k in ('all', 'signed', 'missing')}, {'all': 1, 'signed': 0, 'missing': 1})
        # a test client marked real brings its past agreements into the register
        self.assertEqual(self.post(f'/api/clients/{self.test_client}/test', {'test': False}).status_code, 200)
        rows = {r['name']: r for r in self.register()['clients']}
        self.assertEqual((rows['Practice Clinic']['baa']['source'], rows['Practice Clinic']['covered']), ('manual', True))

    def test_access_and_pages(self):
        self.client(self.u1, 'U1 Clinic')
        self.client(self.u2, 'U2 Clinic')
        self.client(self.ehs_user, 'EHS Clinic', entity=EHS)
        names = lambda: sorted(r['name'] for r in self.register()['clients'])  # noqa: E731
        self.login(self.u1)
        self.assertEqual(names(), ['U1 Clinic'])
        self.login(self.admin)
        self.assertEqual(names(), ['U1 Clinic', 'U2 Clinic'])
        self.login(self.super)
        self.assertEqual(names(), ['EHS Clinic', 'U1 Clinic', 'U2 Clinic'])
        self.login(self.ehs_user)
        self.assertEqual(self.api.get('/api/client-records/register', params={'company': SCRIBE}).status_code, 403)
        self.assertEqual(self.api.get('/api/client-records/register').status_code, 200)
        for i in range(30):
            self.client(self.u1, f'Paged Clinic {i:02d}')
        self.login(self.u1)
        first, second = self.register(), self.register(page=1)
        self.assertEqual((len(first['clients']), len(second['clients']), first['total']), (25, 6, 31))
        self.assertEqual(first['clients'][0]['client_number'], 'CL-0001')

    def test_profile_lists_every_agreement(self):
        cid = self.client(self.u1, 'Everything Clinic')
        contract_id, _ = self.contract(self.u1)
        with db.engine.begin() as c:
            c.execute(update(db.contracts).where(db.contracts.c.id == contract_id).values(client_id=cid))
        self.envelope(self.u1, cid, 'completed', kinds=('baa',))
        self.envelope(self.u1, cid, 'sent', kinds=('services',))
        self.login(self.u1)
        self.past(cid, source='manual', signed_on='2023-06-12')
        items = self.api.get(f'/api/clients/{cid}/agreements').json()['items']
        summary = sorted((x['origin'], x['kind'], x['status']) for x in items)
        self.assertEqual(summary, [('generated', 'baa', 'generated'), ('generated', 'services', 'generated'), ('past', 'baa', 'signed'),
                                   ('portal', 'baa', 'signed'), ('portal', 'services', 'out')])
        signed = next(x for x in items if x['origin'] == 'portal' and x['status'] == 'signed')
        self.assertEqual([link['label'] for link in signed['links']], ['Signed PDF', 'Certificate'])
        self.assertEqual(signed['client_signer'], 'Jane Example')
        generated = next(x for x in items if x['origin'] == 'generated' and x['kind'] == 'services')
        self.assertTrue(generated['title'].startswith('Services Agreement · '), generated['title'])
        self.assertEqual(generated['effective_on'], '2026-10-05')


# ------------------------------------------------------------------ the Signed BAA register
class BaaRegister(Records):
    def test_register_exports_and_zip(self):
        signed = self.client(self.u1, 'Signed Portal Clinic')
        self.envelope(self.u1, signed, 'completed', signer='Maria Torres')
        earlier = self.client(self.u1, 'Signed Earlier Clinic')
        missing = self.client(self.u1, 'Unsigned Clinic')
        self.envelope(self.u1, missing, 'sent', kinds=('services',))
        practice = self.client(self.u1, 'Practice Clinic', test=True)
        self.login(self.u1)
        fid = self.upload(earlier, docusign_baa(company='SCRIBE ALIGN LLC'))
        self.past(earlier, signed_on='2022-03-04', effective_on='2022-03-01', client_signer='Dana Whitfield, Owner', file_id=fid, reference=ENVELOPE_ID)
        self.past(practice, source='manual')
        d = self.api.get('/api/client-records/baa-register').json()
        self.assertEqual(d['company'], SCRIBE)
        self.assertEqual([(x['name'], x['signed'], x['total']) for x in d['tabs']], [(SCRIBE, 2, 3)])
        self.assertEqual((d['kpis']['portal'], d['kpis']['earlier'], d['kpis']['missing'], d['coverage']), (1, 1, 1, 67))
        rows = {r['name']: r for r in d['rows']}
        self.assertEqual(set(rows), {'Signed Portal Clinic', 'Signed Earlier Clinic', 'Unsigned Clinic'})
        self.assertEqual((rows['Signed Portal Clinic']['source'], rows['Signed Portal Clinic']['signed_by']), ('portal', 'Maria Torres'))
        e = rows['Signed Earlier Clinic']
        self.assertEqual((e['date'], e['effective_on'], e['signed_by'], e['files'][0]['url']), ('2022-03-04', '2022-03-01', 'Dana Whitfield, Owner', f'/api/clients/files/{fid}'))
        self.assertFalse(rows['Unsigned Clinic']['signed'])
        self.assertEqual([r['name'] for r in self.api.get('/api/client-records/baa-register', params={'status': 'missing'}).json()['rows']], ['Unsigned Clinic'])
        # exports
        x = self.api.get('/api/client-records/baa-register/export', params={'company': SCRIBE, 'format': 'xlsx'})
        self.assertEqual(x.status_code, 200, x.text)
        from openpyxl import load_workbook
        sheet = load_workbook(io.BytesIO(x.content)).worksheets[0]
        values = [[c.value for c in row] for row in sheet.iter_rows()]
        self.assertEqual(values[0][:4], ['Client no.', 'Client', 'Company', 'BAA signed'])
        self.assertEqual(sorted(v[1] for v in values[1:]), ['Signed Earlier Clinic', 'Signed Portal Clinic', 'Unsigned Clinic'])
        c = self.api.get('/api/client-records/baa-register/export', params={'company': SCRIBE, 'format': 'csv'})
        self.assertIn('Signed Earlier Clinic', c.content.decode('utf-8-sig'))
        z = self.api.get('/api/client-records/baa-register/files.zip', params={'company': SCRIBE})
        self.assertEqual(z.status_code, 200, z.text)
        names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
        self.assertEqual(len([n for n in names if 'Certificate' not in n]), 2)
        self.assertTrue(any(n.startswith('CL-0002 Signed Earlier Clinic - BAA - 2022-03-04') for n in names), names)
        self.assertTrue(any('Certificate of Completion' in n for n in names))
        self.assertEqual(list(db.DATA.glob('baa-*.zip')), [], 'the temporary zip is removed')
        with db.engine.connect() as conn:
            self.assertEqual(conn.execute(select(func.count()).select_from(db.audit).where(db.audit.c.action.in_(
                ('client.baa_register_exported', 'client.baa_files_downloaded')))).scalar(), 3)
        # company permissions
        self.login(self.ehs_user)
        self.assertEqual(self.api.get('/api/client-records/baa-register', params={'company': SCRIBE}).status_code, 403)
        self.assertEqual(self.api.get('/api/client-records/baa-register/export', params={'company': SCRIBE}).status_code, 403)
        self.assertEqual(self.api.get('/api/client-records/baa-register/files.zip', params={'company': SCRIBE}).status_code, 403)
        self.assertEqual([x['name'] for x in self.api.get('/api/client-records/baa-register').json()['tabs']], [EHS])
        self.login(self.u2)
        self.assertEqual(self.api.get('/api/client-records/baa-register').json()['rows'], [], 'another user does not see these clients')


# ------------------------------------------------------------------ import previous clients
class Import(Records):
    def start(self):
        r = self.post('/api/client-imports', {})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def add_file(self, session, name, raw):
        r = self.post(f"/api/client-imports/{session['id']}/files", {'name': name, 'data': b64(raw)})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def count_clients(self):
        with db.engine.connect() as c:
            return c.execute(select(func.count()).select_from(db.clients)).scalar()

    def test_preview_choices_commit(self):
        bluebonnet = self.client(self.admin, 'Bluebonnet Behavioral Health, PLLC')
        self.client(self.admin, 'Prairie Dental Group')
        self.client(self.admin, 'Prairie Dental Care')
        existing = self.client(self.admin, 'Evergreen Psychiatry Associates')
        self.login(self.u1)
        self.assertEqual(self.post('/api/client-imports', {}).status_code, 403, 'Users do not import')
        self.assertEqual(self.api.get('/api/client-imports/template.xlsx').status_code, 403)
        self.login(self.admin)
        template = self.api.get('/api/client-imports/template.xlsx')
        from openpyxl import load_workbook
        self.assertEqual([c.value for c in load_workbook(io.BytesIO(template.content)).worksheets[0][1]],
                         ['Client name', 'Address', 'NPI', 'Email', 'Company', 'Agreement', 'Signed on', 'Signed through', 'Effective date', 'Client signer', 'Notes'])
        session = self.start()
        sheet = io.StringIO()
        w = csv.writer(sheet)
        w.writerow(['Practice name', 'Address', 'NPI', 'Company', 'Agreement', 'Signed date', 'Method', 'Unknown column'])
        w.writerow(['Evergreen Psychiatry Associates', '1 Main St, Rexburg, ID 83440', '1457320981', 'Scribe Align', 'BAA', '2025-07-21', 'Manual', 'x'])
        w.writerow(['Lakeview Pediatrics LLC', '5 Lake Rd, Atlanta, GA 30301', '', 'Scribe Align', 'BAA', '8/9/2023', 'DocuSign', ''])
        r = self.post(f"/api/client-imports/{session['id']}/sheet", {'name': 'previous-clients.csv', 'data': b64(sheet.getvalue().encode())})
        self.assertEqual(r.status_code, 200, r.text)
        mapping = dict(r.json()['sheet']['mapping'])
        self.assertEqual((mapping['Practice name'], mapping['Signed date'], mapping['Method'], mapping['Unknown column']), ('Client name', 'Signed on', 'Signed through', ''))
        blue, scan = docusign_baa(company='SCRIBE ALIGN LLC'), pdf_with([[]], image_only=True)
        found = self.add_file(session, 'Bluebonnet BAA - signed.pdf', blue)
        self.assertEqual(found['found']['source'], 'docusign')
        self.add_file(session, 'Bluebonnet SA - signed.pdf', pdf_with([[f'Docusign Envelope ID: {ENVELOPE_ID}', 'SERVICES AGREEMENT', 'Scribe Align LLC', 'Signed: 3/4/2022']]))
        self.add_file(session, 'Harborview Chiro - BAA (signed).pdf', scan)
        self.add_file(session, 'Prairie Dental - agreement.pdf', pdf_with([['SERVICES AGREEMENT', 'Scribe Align LLC', 'Date: 10/11/2022']]))
        self.add_file(session, 'Older Clinic BAA.pdf', pdf_with([['BUSINESS ASSOCIATE AGREEMENT', 'Scribe Align LLC', 'Date: 04/02/2020']]))
        dup = self.post(f"/api/client-imports/{session['id']}/files", {'name': 'again.pdf', 'data': b64(scan)})
        self.assertEqual(dup.status_code, 409)
        self.assertEqual(self.post(f"/api/client-imports/{session['id']}/files", {'name': 'x.txt', 'data': b64(b'hello')}).status_code, 400)
        view = self.api.get(f"/api/client-imports/{session['id']}").json()
        items = {(i['file'] or {}).get('name') or f"row {i['row']}": i for i in view['items']}
        self.assertEqual(items['Bluebonnet BAA - signed.pdf']['match']['state'], 'matched')
        self.assertEqual(items['Bluebonnet BAA - signed.pdf']['match']['reason'], 'name in file')
        self.assertEqual(items['Bluebonnet BAA - signed.pdf']['match']['client']['id'], bluebonnet)
        self.assertEqual((items['Bluebonnet SA - signed.pdf']['match']['state'], items['Bluebonnet SA - signed.pdf']['match']['reason']), ('matched', 'envelope'))
        harbor = items['Harborview Chiro - BAA (signed).pdf']
        self.assertEqual((harbor['match']['state'], harbor['match']['new'], harbor['text']), ('new', 'Harborview Chiro', False))
        self.assertEqual(harbor['agreement']['kind'], 'baa')
        prairie = items['Prairie Dental - agreement.pdf']
        self.assertEqual((prairie['match']['state'], len(prairie['match']['candidates'])), ('choose', 2))
        self.assertEqual(items['row 2']['match']['client']['id'], existing)
        self.assertEqual((items['row 3']['match']['state'], items['row 3']['match']['new']), ('new', 'Lakeview Pediatrics LLC'))
        self.assertEqual(view['counts']['choose'], 1)
        # nothing is saved before Import
        before = self.count_clients()
        self.assertEqual(self.post(f"/api/client-imports/{session['id']}/commit").status_code, 400)
        self.assertEqual(self.count_clients(), before)
        # choose the client for the ambiguous file
        group = next(c for c in prairie['match']['candidates'] if c['name'] == 'Prairie Dental Group')
        r = self.post(f"/api/client-imports/{session['id']}/choices", {'key': prairie['key'], 'client_id': group['id']})
        self.assertEqual(r.json()['counts']['choose'], 0)
        # the new clients get the next numbers, oldest signature first (Older Clinic 2020, Lakeview 2023, Harborview: no date)
        self.assertEqual([(n['name'], n['number']) for n in r.json()['new_clients']], [('Older Clinic', 'CL-0005'), ('Lakeview Pediatrics LLC', 'CL-0006'),
                                                                                      ('Harborview Chiro', 'CL-0007')])
        done = self.post(f"/api/client-imports/{session['id']}/commit")
        self.assertEqual(done.status_code, 200, done.text)
        self.assertEqual((done.json()['clients_created'], done.json()['agreements'], done.json()['files']), (3, 7, 5))
        self.assertEqual(done.json()['details_filled'], 1)
        with db.engine.connect() as c:
            numbers = dict(c.execute(select(db.clients.c.name, db.clients.c.client_number)).all())
            self.assertEqual((numbers['Older Clinic'], numbers['Lakeview Pediatrics LLC'], numbers['Harborview Chiro']), ('CL-0005', 'CL-0006', 'CL-0007'))
            ever = c.execute(select(db.clients.c.address, db.clients.c.profile).where(db.clients.c.id == existing)).one()
            self.assertEqual((ever[0], ever[1]['npi']), ('1 Main St, Rexburg, ID 83440', '1457320981'))
            blue_rows = c.execute(select(t.client_agreements.c.kind, t.client_agreements.c.source, t.client_agreements.c.reference, t.client_agreements.c.signed_on)
                             .where(t.client_agreements.c.client_id == bluebonnet).order_by(t.client_agreements.c.kind)).all()
            self.assertEqual([tuple(x) for x in blue_rows], [('baa', 'docusign', ENVELOPE_ID, '2022-03-04'), ('services', 'docusign', ENVELOPE_ID, '2022-03-04')])
            self.assertEqual(c.execute(select(func.count()).select_from(t.client_files).where(t.client_files.c.category == 'contract')).scalar(), 5)
            self.assertEqual(c.execute(select(func.count()).select_from(t.client_imports)).scalar(), 0, 'the staging area is cleaned')
            self.assertEqual(c.execute(select(func.count()).select_from(t.client_import_files)).scalar(), 0)
            self.assertEqual(c.execute(select(func.count()).select_from(db.audit).where(db.audit.c.action == 'client.import_committed')).scalar(), 1)
        # importing the same files again finds them already on file
        session = self.start()
        self.add_file(session, 'Bluebonnet BAA - signed.pdf', blue)
        view = self.api.get(f"/api/client-imports/{session['id']}").json()
        self.assertEqual((view['items'][0]['match']['state'], view['items'][0]['match']['reason']), ('skip', 'already on file'))

    def test_cancel_expiry_and_owner(self):
        self.login(self.admin)
        session = self.start()
        self.add_file(session, 'Somebody BAA.pdf', pdf_with([['BUSINESS ASSOCIATE AGREEMENT', 'Scribe Align LLC']]))
        self.assertEqual(self.start()['id'], session['id'], 'the import in progress is reopened')
        self.login(self.super)
        self.assertEqual(self.api.get(f"/api/client-imports/{session['id']}").status_code, 404, 'only its owner sees an import')
        self.login(self.admin)
        self.assertEqual(self.post(f"/api/client-imports/{session['id']}/cancel").status_code, 200)
        with db.engine.connect() as c:
            self.assertEqual(c.execute(select(func.count()).select_from(t.client_import_files)).scalar(), 0)
        session = self.start()
        self.add_file(session, 'Old BAA.pdf', pdf_with([['BUSINESS ASSOCIATE AGREEMENT']]))
        with db.engine.begin() as c:
            c.execute(update(t.client_imports).values(expires=time.time() - 1))
        self.assertEqual(self.api.get(f"/api/client-imports/{session['id']}").status_code, 404)
        fresh = self.start()
        self.assertNotEqual(fresh['id'], session['id'])
        with db.engine.connect() as c:
            self.assertEqual(c.execute(select(func.count()).select_from(t.client_imports)).scalar(), 1, 'expired imports are removed')
            self.assertEqual(c.execute(select(func.count()).select_from(t.client_import_files)).scalar(), 0)


if __name__ == '__main__':
    unittest.main()

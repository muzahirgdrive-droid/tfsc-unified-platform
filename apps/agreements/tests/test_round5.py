"""Round 5: Trash and permanent client numbers, the register's agreement columns and bulk actions, the signed agreements
register for every agreement type, subjects that name every document, emails in the company's name, the running footer and
letterhead artwork, and Billing Nest's corrected letterhead."""
import io
import json
import re
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import client_trash
import database as db
import document_engine
import entities as entmod
import esign_db as t
import migrate
import server as legacy
from alembic import command
from sqlalchemy import create_engine, exc, select, text, update
from test_client_records import Records
from test_contract_studio import data
from test_esign import SCRIBE

APP = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------ Trash, restore, purge
class Trash(Records):
    def delete(self, ids, status=200):
        r = self.post('/api/client-records/delete', {'ids': ids})
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def test_only_a_super_admin_deletes_restores_or_sees_the_trash(self):
        cid = self.client(self.u1, 'Guarded Clinic')
        for who in (self.u1, self.admin):
            self.login(who)
            self.assertEqual(self.post('/api/client-records/delete', {'ids': [cid]}).status_code, 403)
            self.assertEqual(self.post('/api/client-records/restore', {'ids': [cid]}).status_code, 403)
            self.assertEqual(self.api.get('/api/client-records/trash').status_code, 403)
            self.assertEqual(self.api.get('/api/client-records/delete-preview', params={'ids': cid}).status_code, 403)
            self.assertFalse(self.register()['can_delete'])
        self.login(self.super)
        self.assertTrue(self.register()['can_delete'])

    def test_delete_hides_everywhere_and_restore_brings_it_back_with_the_same_number(self):
        a, b, keep = self.client(self.u1, 'Alpha Clinic'), self.client(self.u1, 'Bravo Clinic'), self.client(self.u1, 'Charlie Clinic')
        waiting = self.envelope(self.u1, a, 'sent')
        with db.engine.connect() as c:
            numbers = dict(c.execute(select(db.clients.c.id, db.clients.c.client_number)).all())
        self.login(self.super)
        preview = self.api.get('/api/client-records/delete-preview', params={'ids': f'{a},{b}'}).json()
        self.assertEqual([x['id'] for x in preview['clients']], [a, b])
        self.assertEqual(preview['days'], 30)
        self.assertEqual(preview['clients'][0]['waiting'][0]['id'], waiting, 'the confirmation names the envelope still out for signature')
        self.assertEqual(self.delete([a, b])['deleted'], 2)
        names = [x['name'] for x in self.register()['clients']]
        self.assertNotIn('Alpha Clinic', names)
        self.assertIn('Charlie Clinic', names)
        self.assertEqual(self.register()['trash'], 2)
        self.assertNotIn(a, [x['id'] for x in self.api.get('/api/clients').json()['clients']], 'gone from Create agreement and pickers')
        r = self.api.get(f'/api/clients/{a}/profile')
        self.assertEqual(r.status_code, 409)
        self.assertIn('Trash', r.json()['error'])
        self.assertEqual(self.api.get(f'/api/clients/{a}').status_code, 409)
        self.delete([a], status=404)                                    # already in the Trash
        trash = self.api.get('/api/client-records/trash').json()
        self.assertEqual({x['id'] for x in trash['clients']}, {a, b})
        self.assertTrue(all(x['days_left'] == 30 for x in trash['clients']))
        # the envelope out for signature is untouched and still listed
        with db.engine.connect() as c:
            self.assertEqual(c.execute(select(t.envelopes.c.status).where(t.envelopes.c.id == waiting)).scalar(), 'sent')
        r = self.post('/api/client-records/restore', {'ids': [a]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['clients'][0]['client_number'], numbers[a])
        back = {x['id']: x['client_number'] for x in self.register()['clients']}
        self.assertEqual(back[a], numbers[a], 'restored with its original number')
        self.assertNotIn(b, back)
        self.assertEqual(back[keep], numbers[keep])
        with db.engine.connect() as c:
            actions = [r[0] for r in c.execute(select(db.audit.c.action).where(db.audit.c.client_id == a).order_by(db.audit.c.created))]
        self.assertIn('client.deleted', actions)
        self.assertIn('client.restored', actions)

    def test_a_name_in_the_trash_is_not_taken_over_by_a_new_client_or_a_generation(self):
        cid = self.client(self.super, 'Delta Clinic')
        self.login(self.super)
        self.delete([cid])
        r = self.post('/api/clients/new', {'name': 'Delta Clinic', 'entity': SCRIBE})
        self.assertEqual(r.status_code, 409)
        self.assertIn('Trash', r.json()['error'])
        p = dict(data(), client_name='Delta Clinic', output_root=str(db.OUTPUT), include_signature=False)
        r = self.post('/api/clients', p)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn('Trash', r.json()['error'])

    def test_purge_after_thirty_days_keeps_signed_records_and_retires_the_number(self):
        cid = self.client(self.super, 'Echo Clinic', address='1 Main St, Austin, TX 78701')
        signed = self.envelope(self.super, cid, 'completed', kinds=('baa', 'services'))
        self.login(self.super)
        self.past(cid, kind='services', signed_on='2021-05-01')
        self.assertEqual(self.post(f'/api/clients/{cid}/contacts', {'name': 'Pat', 'email': 'pat@echo.test', 'role': 'billing'}).status_code, 200)
        with db.engine.connect() as c:
            number = c.execute(select(db.clients.c.client_number).where(db.clients.c.id == cid)).scalar()
        self.delete([cid])
        with db.engine.begin() as c:
            self.assertEqual(client_trash.purge_due(c), 0, 'not before 30 days')
            c.execute(update(db.clients).where(db.clients.c.id == cid).values(deleted_at=time.time() - 31 * 86400))
            self.assertEqual(client_trash.purge_due(c), 1)
            row = c.execute(select(db.clients).where(db.clients.c.id == cid)).mappings().one()
            self.assertTrue(row['purged_at'])
            self.assertEqual((row['address'], row['client_number']), ('', number))
            self.assertEqual(c.execute(select(t.client_contacts.c.id).where(t.client_contacts.c.client_id == cid)).all(), [])
            self.assertEqual(c.execute(select(t.envelopes.c.status).where(t.envelopes.c.id == signed)).scalar(), 'completed', 'signed agreements are kept')
            self.assertEqual(len(c.execute(select(t.client_agreements.c.id).where(t.client_agreements.c.client_id == cid)).all()), 1)
            self.assertEqual(c.execute(select(db.client_number_registry.c.client_id).where(db.client_number_registry.c.number == number)).scalar(), cid)
            self.assertEqual(client_trash.purge_due(c), 0, 'purged once')
        self.assertEqual(self.api.get('/api/client-records/trash').json()['clients'], [], 'no longer in the Trash')
        d = self.api.get(f'/api/esign/envelopes/{signed}')
        self.assertEqual(d.status_code, 200, 'the signed envelope still opens under Agreements')
        self.assertEqual(d.json()['status'], 'completed')
        r = self.post('/api/clients/new', {'name': 'Echo Clinic', 'entity': SCRIBE})
        self.assertEqual(r.status_code, 200, 'the name is free again')
        self.assertNotEqual(r.json()['client_number'], number, 'the number is never given again')
        with db.engine.connect() as c:
            self.assertEqual(c.execute(select(db.audit.c.action).where(db.audit.c.client_id == cid, db.audit.c.action == 'client.purged')).scalar(), 'client.purged')


# ------------------------------------------------------------------ client numbers kept by the database itself
class NumbersInTheDatabase(Records):
    def test_a_number_never_changes_and_is_never_reused(self):
        a, b = self.client(self.u1, 'Foxtrot Clinic'), self.client(self.u1, 'Golf Clinic')
        with db.engine.connect() as c:
            na = c.execute(select(db.clients.c.client_number).where(db.clients.c.id == a)).scalar()
        with self.assertRaises(exc.DBAPIError):
            with db.engine.begin() as c:
                c.execute(update(db.clients).where(db.clients.c.id == a).values(client_number='CL-9000'))
        with self.assertRaises(exc.DBAPIError):
            with db.engine.begin() as c:
                c.execute(update(db.clients).where(db.clients.c.id == a).values(client_number=None))
        with self.assertRaises(exc.DBAPIError):
            with db.engine.begin() as c:
                c.execute(db.clients.insert().values(id=db.uid(), owner_id=self.u1['id'], name='Hotel', name_key='kh', address='', draft={}, updated=1, client_number=na))
        with self.assertRaises(exc.DBAPIError):
            with db.engine.begin() as c:
                c.execute(db.client_number_registry.delete().where(db.client_number_registry.c.number == na))
        with self.assertRaises(exc.DBAPIError):
            with db.engine.begin() as c:
                c.execute(db.client_number_registry.update().where(db.client_number_registry.c.number == na).values(client_id=b))
        # a number written directly (by an older release, or an import) is registered by the database itself
        cid = db.uid()
        with db.engine.begin() as c:
            c.execute(db.clients.insert().values(id=cid, owner_id=self.u1['id'], name='India', name_key='ki', address='', draft={}, updated=1, client_number='CL-0777'))
            self.assertEqual(c.execute(select(db.client_number_registry.c.client_id).where(db.client_number_registry.c.number == 'CL-0777')).scalar(), cid)
            # even with the counter lost and the client gone, numbering continues after every number ever given
            c.execute(db.settings.delete().where(db.settings.c.key == 'client_numbers'))
            c.execute(db.clients.delete().where(db.clients.c.id == cid))
        fresh = self.client(self.u1, 'Juliet Clinic')
        with db.engine.connect() as c:
            self.assertEqual(c.execute(select(db.clients.c.client_number).where(db.clients.c.id == fresh)).scalar(), 'CL-0778')


class MigrationFor0011(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='tfsc-r5-')
        self.engine = create_engine('sqlite:///' + str(Path(self.tmp.name) / 'm.db'))

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def test_backfill_keeps_numbers_of_clients_already_gone_and_downgrade_is_clean(self):
        with self.engine.begin() as c:
            command.upgrade(migrate.config(c), '0010_billing_nest_letterhead')
            c.execute(text("INSERT INTO users (id, email, name, password, role, admin_id, companies, active, reset_required, created) VALUES ('u', 'a@b.c', 'A', 'x', 'user', NULL, '[]', 1, 0, 1)"))
            for i, n in ((1, 'CL-0001'), (3, 'CL-0003')):
                c.execute(text("INSERT INTO clients (id, owner_id, name, name_key, address, draft, updated, client_number) VALUES (:i, 'u', :n, :n, '', '{}', 1, :n)"), {'i': f'c{i}', 'n': n})
            c.execute(text("DELETE FROM settings WHERE key = 'client_numbers'"))
            c.execute(text("INSERT INTO settings (key, value) VALUES ('client_numbers', '{\"last\": 4}')"))
        with self.engine.begin() as c:
            command.upgrade(migrate.config(c), 'head')
            reg = dict(c.execute(text('SELECT number, client_id FROM client_number_registry')).all())
            self.assertEqual(reg, {'CL-0001': 'c1', 'CL-0002': '', 'CL-0003': 'c3', 'CL-0004': ''})
            self.assertEqual(c.execute(text('SELECT count(*) FROM clients WHERE deleted_at IS NULL')).scalar(), 2)
        with self.engine.begin() as c:     # running it again after a rollback adds nothing
            c.execute(text("UPDATE alembic_version SET version_num='0010_billing_nest_letterhead'"))
        with self.engine.begin() as c:
            command.upgrade(migrate.config(c), 'head')
            self.assertEqual(c.execute(text('SELECT count(*) FROM client_number_registry')).scalar(), 4)
        with self.engine.begin() as c:
            command.downgrade(migrate.config(c), '0010_billing_nest_letterhead')
            self.assertNotIn('client_number_registry', {r[0] for r in c.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))})
            self.assertEqual(c.execute(text("SELECT count(*) FROM sqlite_master WHERE type='trigger'")).scalar(), 0)
            c.execute(text("UPDATE clients SET client_number='CL-0009' WHERE id='c1'"))   # no trigger left behind


# ------------------------------------------------------------------ the register, bulk actions, signed agreements register
class RegisterAndBulk(Records):
    def test_each_agreement_has_its_own_status_and_filters_count_them(self):
        both, baa_only, waiting = self.client(self.u1, 'Kilo Clinic'), self.client(self.u1, 'Lima Clinic'), self.client(self.u1, 'Mike Clinic')
        self.envelope(self.u1, both, 'completed', kinds=('baa', 'services'))
        self.envelope(self.u1, baa_only, 'completed', kinds=('baa',))
        self.envelope(self.u1, waiting, 'sent', kinds=('services',))
        self.login(self.u1)
        d = self.register()
        rows = {r['name']: r['agreements'] for r in d['clients']}
        self.assertEqual(rows['Kilo Clinic']['services']['state'], 'signed')
        self.assertTrue(rows['Kilo Clinic']['services']['date'])
        self.assertEqual(rows['Lima Clinic']['services']['state'], 'none')
        self.assertEqual(rows['Mike Clinic']['services']['state'], 'out')
        self.assertTrue(rows['Mike Clinic']['services']['sent'])
        self.assertEqual((d['counts']['both'], d['counts']['waiting'], d['counts']['no_services']), (1, 1, 2))
        self.assertEqual(d['kpis']['services_signed'], 1)
        self.assertEqual([r['name'] for r in self.register(show='both')['clients']], ['Kilo Clinic'])
        self.assertEqual([r['name'] for r in self.register(show='waiting')['clients']], ['Mike Clinic'])

    def test_bulk_mark_and_export(self):
        a, b = self.client(self.u1, 'November Clinic'), self.client(self.u1, 'Oscar Clinic')
        self.envelope(self.u1, a, 'completed', kinds=('baa',))
        self.login(self.u1)
        r = self.post('/api/client-records/mark', {'ids': [a, b], 'test': True})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['changed'], 2)
        self.assertEqual(self.register(test='test')['total'], 2)
        self.assertEqual(self.post('/api/client-records/mark', {'ids': [a, b], 'test': False}).json()['changed'], 2)
        r = self.api.post('/api/client-records/export', json={'ids': [a, b], 'format': 'csv'}, headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(r.status_code, 200, r.text)
        lines = r.content.decode('utf-8-sig').splitlines()
        self.assertIn('Services Agreement', lines[0])
        self.assertIn('November Clinic', lines[1])
        self.assertIn('Signed', lines[1])
        other = self.client(self.ehs_user, 'Papa Clinic', entity='EXTENDED HEALTH SERVICES INC.')
        self.assertEqual(self.post('/api/client-records/mark', {'ids': [other], 'test': True}).status_code, 404, 'only clients the person may change')

    def test_signed_agreements_register_for_services(self):
        a = self.client(self.u1, 'Quebec Clinic')
        self.envelope(self.u1, a, 'completed', kinds=('baa', 'services'))
        self.login(self.super)
        d = self.api.get('/api/client-records/baa-register', params={'kind': 'services', 'company': SCRIBE}).json()
        self.assertEqual((d['kind'], d['short']), ('services', 'Services Agreement'))
        self.assertEqual(d['types']['services']['signed'], 1)
        row = next(r for r in d['rows'] if r['name'] == 'Quebec Clinic')
        self.assertTrue(row['signed'])
        self.assertFalse(row['out'])
        b = self.client(self.u1, 'Romeo Clinic')
        self.envelope(self.u1, b, 'sent', kinds=('baa', 'services'))
        d = self.api.get('/api/client-records/baa-register', params={'kind': 'services', 'company': SCRIBE}).json()
        row = next(r for r in d['rows'] if r['name'] == 'Romeo Clinic')
        self.assertEqual((row['signed'], row['out']), (False, True), 'out for signature, not "Not signed"')
        self.assertTrue(row['sent'])
        r = self.api.get('/api/client-records/baa-register/export', params={'kind': 'services', 'company': SCRIBE, 'format': 'csv'})
        self.assertIn('Services Agreement signed', r.content.decode('utf-8-sig').splitlines()[0])
        z = self.api.get('/api/client-records/baa-register/files.zip', params={'kind': 'services', 'company': SCRIBE})
        self.assertEqual(z.status_code, 200)
        names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
        self.assertTrue(any('Services Agreement' in n for n in names), names)
        from urllib.parse import unquote
        self.assertIn('Signed Services Agreements', unquote(z.headers['content-disposition']))


# ------------------------------------------------------------------ subjects and the company's name
class SubjectsAndSender(Records):
    def test_the_subject_names_every_document_until_the_sender_types_their_own(self):
        self.connect_mailbox()
        contract_id, client_id = self.contract(self.u1)
        self.login(self.u1)
        env = self.post('/api/esign/envelopes', {'entity': SCRIBE, 'client_id': client_id}).json()
        lib = self.api.get(f"/api/esign/envelopes/{env['id']}/library").json()['items']
        baa = next(x for x in lib if x.get('agreement') == 'baa')
        services = next(x for x in lib if x.get('agreement') == 'services')
        d = self.post(f"/api/esign/envelopes/{env['id']}/library", {'source': baa['source'], 'id': baa['id']}).json()
        self.assertTrue(d['subject'].startswith('Please Sign: Business Associate Agreement - '))
        d = self.post(f"/api/esign/envelopes/{env['id']}/library", {'source': services['source'], 'id': services['id']}).json()
        self.assertTrue(d['subject'].startswith('Please Sign: Business Associate Agreement and Services Agreement - '), d['subject'])
        self.assertIs(d['settings']['subject_auto'], True)
        # the sender types a subject: it is kept as typed, also when documents change
        d = self.post(f"/api/esign/envelopes/{env['id']}/draft", {'version': d['version'], 'subject': 'Our agreements for you'}).json()
        self.assertEqual(d['subject'], 'Our agreements for you')
        self.assertIs(d['settings']['subject_auto'], False)
        doc = d['documents'][-1]['id']
        d = self.post(f"/api/esign/envelopes/{env['id']}/documents/{doc}/remove").json()
        self.assertEqual(d['subject'], 'Our agreements for you')
        # and back to the automatic subject
        d = self.post(f"/api/esign/envelopes/{env['id']}/draft", {'version': d['version'], 'subject': d['subject'], 'subject_auto': True}).json()
        self.assertTrue(d['subject'].startswith('Please Sign: Business Associate Agreement - '))

    def test_recipients_hear_from_the_company_not_the_person(self):
        self.connect_mailbox()
        contract_id, client_id = self.contract(self.u1)
        self.login(self.u1)
        env = self.post('/api/esign/envelopes', {'contract_id': contract_id}).json()
        self.assertIn('Business Associate Agreement and Services Agreement', env['subject'])
        r = self.post(f"/api/esign/envelopes/{env['id']}/send")
        self.assertEqual(r.status_code, 200, r.text)
        self.drain()
        invite = self.smtp.to('jane@client.test')[-1]
        html = invite['msg'].get_body(preferencelist=('html',)).get_content()
        plain = invite['msg'].get_body(preferencelist=('plain',)).get_content()
        self.assertIn('Scribe Align LLC sent you documents to review and sign.', plain)
        self.assertIn('REVIEW DOCUMENTS', html)
        self.assertNotIn(self.u1['name'], plain + html)
        self.assertEqual(invite['msg']['Subject'], env['subject'])
        client, base, state = self.signer(self.smtp.link(invite))
        self.assertEqual(state['sender'], 'Scribe Align LLC')
        self.assertEqual(self.complete_signing(client, base, state).status_code, 200)
        self.drain()
        self.drain()
        with db.engine.connect() as c:
            cert = c.execute(select(t.envelopes.c.certificate).where(t.envelopes.c.id == env['id'])).scalar()
        from pypdf import PdfReader
        words = ' '.join(p.extract_text() for p in PdfReader(io.BytesIO(bytes(cert))).pages)
        self.assertIn('Scribe Align LLC', words)
        self.assertNotIn(self.u1['name'], words)


# ------------------------------------------------------------------ agreements: running footer, letterhead artwork, Billing Nest
class Layout(Records):
    def footer_and_header(self, name, renderer='libreoffice'):
        import os
        from unittest.mock import patch
        entmod.invalidate()
        with db.engine.begin() as c:
            c.execute(db.companies.update().values(signatory='Sample Signatory', title='Member'))
        d = legacy.validate(dict(data(), entity=name, document_types=['services'], services=[dict(id='medical_billing', model='percentage', rate=5)]))
        import companies
        with db.engine.connect() as c:
            company = companies.profile(c, name)
        legacy.DATA_DIR = db.DATA
        doc = legacy.build_documents(d, 'SA-TEST-9', company)[0]
        out = self.root / f'{entmod.get_entity(name)["key"]}-{renderer}.docx'
        with patch.dict(os.environ, {'TFSC_RENDERER': renderer}):
            document_engine.generate_docx(doc['blocks'], doc['data'], 'SA-TEST-9', out)
        with zipfile.ZipFile(out) as z:
            parts = {n: z.read(n).decode() for n in z.namelist() if re.fullmatch(r'word/(header|footer)\d+\.xml|word/document\.xml', n)}
        return parts

    def test_one_running_footer_in_one_style_on_every_letterhead(self):
        for name in ('SCRIBE ALIGN LLC', '24-7 CONSULTANCY LLC', 'BILLING NEST, LLC'):
            with self.subTest(company=name):
                parts = self.footer_and_header(name)
                footer = ''.join(v for k, v in parts.items() if 'footer' in k)
                self.assertIn('Services Agreement  ·  SA-TEST-9', footer)
                counter = footer[footer.index('Services Agreement'):]
                counter = counter[:counter.index('</w:p>')]
                self.assertEqual(set(re.findall(r'<w:sz w:val="(\d+)"/>', counter)), {'18'}, 'every run of the line, numbers included, is 9 pt')
                self.assertEqual(counter.count('NUMPAGES'), 1)
                self.assertNotIn('<w:b/>', counter)
                self.assertEqual(footer.count('NUMPAGES'), footer.count('Services Agreement  ·'), 'no second counter from the letterhead')

    def test_letterhead_artwork_stays_where_the_letterhead_puts_it(self):
        entity = entmod.get_entity('BILLING NEST, LLC')
        template_left = int(re.search(r'w:left="(\d+)"', zipfile.ZipFile(entity['template_path']).read('word/document.xml').decode()).group(1))
        original = zipfile.ZipFile(entity['template_path']).read('word/footer1.xml').decode()
        before = int(re.search(r'<wp:positionH relativeFrom="column">\s*<wp:posOffset>(-?\d+)', original).group(1))
        parts = self.footer_and_header('BILLING NEST, LLC')
        after = int(re.search(r'<wp:positionH relativeFrom="column">\s*<wp:posOffset>(-?\d+)', parts['word/footer1.xml']).group(1))
        self.assertEqual(after, before - (entity['left'] - template_left) * 635)


class BillingNestLetterhead(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='tfsc-bn-')
        self.engine = create_engine('sqlite:///' + str(Path(self.tmp.name) / 'm.db'))

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def test_the_incorrect_letterhead_is_replaced_and_one_uploaded_since_is_kept(self):
        import hashlib
        manifest = {m['key']: m for m in json.loads((APP / 'assets/companies/companies.json').read_text())}
        with self.engine.begin() as c:
            command.upgrade(migrate.config(c), '0009_five_companies')
            c.execute(text("UPDATE companies SET letterhead=:b, letterhead_sha256=:s WHERE key='billing_nest'"), {'b': b'old', 's': 'b0dedfb1e6dc36d56ddd09bd99a72118a79406edcb7f286c7a211cb620d55911'})
            c.execute(text("UPDATE companies SET letterhead=:b, letterhead_sha256=:s WHERE key='quantum_medical_services'"), {'b': b'mine', 's': hashlib.sha256(b'mine').hexdigest()})
        with self.engine.begin() as c:
            command.upgrade(migrate.config(c), 'head')
            rows = {r[0]: (r[1], r[2]) for r in c.execute(text('SELECT key, letterhead_sha256, bottom FROM companies'))}
        self.assertEqual(rows['billing_nest'], (manifest['billing_nest']['letterhead_sha256'], manifest['billing_nest']['bottom']))
        self.assertEqual(rows['quantum_medical_services'][0], hashlib.sha256(b'mine').hexdigest())


if __name__ == '__main__':
    unittest.main()

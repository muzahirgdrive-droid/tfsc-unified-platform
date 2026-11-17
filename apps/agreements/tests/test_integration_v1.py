"""Release 5.3: the read-only feed for TFS Onboarding and Client Success (TFS Agreements Integration API v1).

Every emitted body is checked against the frozen contract in packages/integration-contract/agreements-v1 (jsonschema).
All data is fictional."""
import copy
import hashlib
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from sqlalchemy import func, select, update

import client_numbers
import client_trash
import database as db
import esign_db as t
import esign_pdf
import integration_v1 as feed
import marks
import portal
import server as legacy
import test_esign
from esign_support import agreement_pdf
from test_contract_studio import data
from test_esign import SCRIBE

Base = test_esign.Base
CONTRACT_DIR = Path(__file__).resolve().parents[3] / 'packages' / 'integration-contract' / 'agreements-v1'
FEED = '/api/integration/v1'


def validators() -> dict:
    schemas = {p.name: json.loads(p.read_text()) for p in CONTRACT_DIR.glob('*.schema.json')}
    registry = Registry()
    for name, s in schemas.items():
        res = Resource.from_contents(s)
        registry = registry.with_resource(s['$id'], res).with_resource(name, res)
    return {n.removesuffix('.schema.json'): Draft202012Validator(s, registry=registry) for n, s in schemas.items()}


V = validators()


def shape(value):
    """The structure of a JSON value: its keys and the types at each place, without the values."""
    if isinstance(value, dict):
        return {k: shape(v) for k, v in value.items()}
    if isinstance(value, list):
        return [shape(value[0])] if value else []
    return type(value).__name__ if value is not None else 'null'


class Feed(Base):
    def setUp(self):
        super().setUp()
        self.feed_api = TestClient(portal.app, base_url='http://127.0.0.1:8765')
        self.token = self.make_token()

    def tearDown(self):
        self.feed_api.close()
        super().tearDown()

    # ------------------------------------------------------------------ fixtures (fictional)
    def make_token(self, name='OCS'):
        self.login(self.super)
        r = self.post('/api/admin/integrations/tokens', {'name': name})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['token']

    def get(self, path, token=None, **params):
        token = self.token if token is None else token
        return self.feed_api.get(FEED + path, params=params, headers={'Authorization': f'Bearer {token}'} if token else {})

    def client(self, name='Northside Family Clinic, PLLC', owner=None, test=False):
        cid = db.uid()
        with db.engine.begin() as c:
            c.execute(db.clients.insert().values(id=cid, owner_id=(owner or self.u1)['id'], name=name, name_key=db.uid(), address='400 Lake Ave, Reno, NV 89501',
                                                 draft={}, updated=time.time(), created=time.time(), test=test,
                                                 profile={'phone': '(775) 555-0142', 'email': 'office@northsidefamily.test', 'website': 'https://northsidefamily.test'}))
            client_numbers.assign_one(c, cid)
            now = time.time()
            for role, person, title, email in (('signatory', 'Dr. Rebecca Moore, MD', 'Owner', 'rebecca.moore@northsidefamily.test'),
                                               ('billing', 'Dana Whitfield', 'Office Manager', 'dana@northsidefamily.test')):
                c.execute(t.client_contacts.insert().values(id=db.uid(), client_id=cid, name=person, email=email, title=title, phone='(775) 555-0143' if role == 'signatory' else '',
                                                            role=role, notes='', created=now, updated=now, created_by=self.u1['id']))
                now += 1
        return cid

    def services_payload(self):
        """A Services Agreement and a BAA: medical billing with two fee options, credentialing on the rate card, one custom service."""
        payload = dict(data(), client_name='Northside Family Clinic, PLLC', entity=SCRIBE, effective_date='2026-03-01', baa_effective_date='2026-03-01',
                       client_signatory='Dr. Rebecca Moore, MD', client_title='Owner', governing_state='Nevada', notice_days=30, payment_days=15, cure_days=10,
                       document_types=['baa', 'services'],
                       services=[{'id': 'medical_billing', 'model': 'percentage', 'rate': 5,
                                  'options': [{'id': 'medical_billing', 'model': 'percentage', 'rate': 5},
                                              {'id': 'medical_billing', 'model': 'fte', 'rate': 1400, 'quantity': 1, 'hours': 40}]},
                                 {'id': 'credentialing', 'model': 'rate_card', 'government_rate': 150, 'commercial_rate': 200, 'details': 'Up to 8 payers for two providers.'}],
                       custom_services=[{'name': 'Practice Launch Support', 'category': 'recurring', 'model': 'fixed', 'rate': 750,
                                         'scope': 'Opening-week support for the front desk.', 'responsibilities': 'Client provides system access.', 'exclusions': 'Clinical work.'}])
        payload = legacy.validate(payload)
        payload['company_signatory'] = {'name': 'Naeem Abbas', 'title': 'Chief Executive Officer', 'signature': 'saved'}
        return payload

    def signed_envelope(self, client_id, payload=None, option='Option B', test=False, status='completed', contract_test=False, agreements=('baa', 'services')):
        """A completed envelope as the portal leaves it: a contract, its signed documents, the signers, the chosen fee option and the certificate."""
        payload = payload or self.services_payload()
        contract_id, env_id, now = 'TFSC-' + db.uid().upper()[:30], db.uid(), time.time()
        names = {'baa': 'Business Associate Agreement', 'services': 'Services Agreement'}
        cert = agreement_pdf()
        with db.engine.begin() as c:
            c.execute(db.contracts.insert().values(id=contract_id, client_id=client_id, owner_id=self.u1['id'], entity=SCRIBE, created=now, payload=payload,
                                                   folder='x', version='4.0.0', legacy=False, test=contract_test))
            c.execute(t.envelopes.insert().values(id=env_id, ref=db.uid()[:12].upper(), owner_id=self.u1['id'], entity=SCRIBE, client_id=client_id, contract_id=contract_id,
                                                  subject='Please sign', message='', status=status, settings={}, version=1, created=now, updated=now, sent_at=now,
                                                  expiry_warned=False, completed_at=now if status == 'completed' else None, completion_attempts=0,
                                                  certificate=cert if status == 'completed' else None, certificate_sha256=esign_pdf.sha256(cert) if status == 'completed' else None,
                                                  test=test))
            docs = []
            for position, kind in enumerate(agreements):
                pdf, final = agreement_pdf(), agreement_pdf()
                doc_id = db.uid()
                c.execute(t.documents.insert().values(id=doc_id, envelope_id=env_id, position=position, name=f'{names[kind]} {contract_id}.pdf', source='generated',
                                                      agreement=kind, pdf=pdf, sha256=esign_pdf.sha256(pdf), pages=2, page_sizes=[[612, 792, 0]] * 2,
                                                      final_pdf=final if status == 'completed' else None, final_sha256=esign_pdf.sha256(final) if status == 'completed' else None,
                                                      created=now))
                docs.append((doc_id, kind))
            people = []
            for order, (role_name, person, email) in enumerate((('Client', 'Dr. Rebecca Moore, MD', 'rebecca.moore@northsidefamily.test'),
                                                                ('Company signer', 'Naeem Abbas', 'naeem@scribealign.test')), 1):
                rid = db.uid()
                c.execute(t.recipients.insert().values(id=rid, envelope_id=env_id, role='signer', role_name=role_name, routing_order=order, name=person, email=email,
                                                       note='', status='completed' if status == 'completed' else 'sent', sent_at=now,
                                                       signed_at=now - 10 if status == 'completed' else None, auth_failures=0, delivery='sent', reminders_sent=0, created=now + order))
                people.append(rid)
            for doc_id, kind in docs:
                c.execute(t.fields.insert().values(id=db.uid(), envelope_id=env_id, document_id=doc_id, recipient_id=people[0], page=2, kind='title', x=1, y=1, w=1, h=1,
                                                   required=True, label='Title', options={}, value='Owner', filled_at=now))
                if kind == 'services' and option:
                    c.execute(t.fields.insert().values(id=db.uid(), envelope_id=env_id, document_id=doc_id, recipient_id=people[0], page=2, kind='dropdown', x=1, y=2, w=1, h=1,
                                                       required=True, label='Fee option for Medical Billing, Including Coding'[:100],
                                                       options={'choices': ['Option A', 'Option B']}, value=option, filled_at=now))
            db.log(c, None, 'esign.completed', client_id, {'envelope': env_id})
        return env_id, contract_id

    def past_agreement(self, client_id, test=False, signed_on='2025-06-12', with_file=True):
        now, file_id, row_id = time.time(), None, db.uid()
        with db.engine.begin() as c:
            if with_file:
                body = agreement_pdf()
                file_id = db.uid()
                c.execute(t.client_files.insert().values(id=file_id, client_id=client_id, name='Northside Services Agreement 2025.pdf', content_type='application/pdf',
                                                         size=len(body), sha256=hashlib.sha256(body).hexdigest(), scan='clean', category='contract', note='', body=body,
                                                         uploaded_by=self.u1['id'], created=now))
            c.execute(t.client_agreements.insert().values(id=row_id, client_id=client_id, entity=SCRIBE, kind='services', title='Services Agreement (DocuSign, 2025)',
                                                          source='docusign', signed_on=signed_on, effective_on='2025-07-01', client_signer='Rebecca Moore',
                                                          company_signer='Naeem Abbas', reference='DS-4411-AB20', notes='', file_id=file_id, created=now,
                                                          created_by=self.u1['id'], updated=now, test=test))
        return row_id

    def everything(self):
        out, after = [], 0
        while True:
            r = self.get('/changes', after=after, limit=200)
            self.assertEqual(r.status_code, 200, r.text)
            page = r.json()
            self.assertFalse(list(V['changes'].iter_errors(page)), page)
            out += page['changes']
            if not page['has_more']:
                return out
            after = page['changes'][-1]['seq']

    def latest(self):
        state = {}
        for ch in self.everything():
            if ch['kind'] == 'client.snapshot':
                state[ch['client_ref']] = ch['snapshot']
            else:
                state.pop(ch['client_ref'], None)
        return state

    # ------------------------------------------------------------------ the golden fixture
    def test_golden_client_produces_the_contract_example_shape_and_validates(self):
        cid = self.client()
        self.signed_envelope(cid, option='Option B')
        self.past_agreement(cid)
        self.assertEqual(feed.publish_once(), 1)
        page = self.get('/changes', after=0, limit=100).json()
        self.assertFalse(list(V['changes'].iter_errors(page)))
        example = json.loads((CONTRACT_DIR / 'examples' / 'changes-page.example.json').read_text())
        mine, theirs = page['changes'][0]['snapshot'], example['changes'][0]['snapshot']
        # the same properties everywhere: client, contacts, each agreement, services, pricing per model, documents
        self.assertEqual(set(page), set(example))
        self.assertEqual(set(page['changes'][0]), set(example['changes'][0]))
        self.assertEqual(set(mine), set(theirs))
        self.assertEqual(shape(mine['contacts']), shape(theirs['contacts']))
        by_kind = {(a['origin'], a['kind']): a for a in mine['agreements']}
        for a in theirs['agreements']:
            self.assertEqual(set(by_kind[(a['origin'], a['kind'])]), set(a), a['agreement_ref'])
            self.assertEqual(shape(by_kind[(a['origin'], a['kind'])]['documents'][0]), shape(a['documents'][0]))
        # the values the fixture fixes
        number = mine['client_number']
        self.assertRegex(number, r'^CL-\d{4,}$')
        self.assertEqual((mine['legal_name'], mine['address'], mine['phone'], mine['email'], mine['website'], mine['state'], mine['is_test']),
                         ('Northside Family Clinic, PLLC', '400 Lake Ave, Reno, NV 89501', '(775) 555-0142', 'office@northsidefamily.test', 'https://northsidefamily.test', 'active', False))
        self.assertEqual([(k['name'], k['title'], k['role']) for k in mine['contacts']], [('Dr. Rebecca Moore, MD', 'Owner', 'signatory'), ('Dana Whitfield', 'Office Manager', 'billing')])
        self.assertEqual([(a['origin'], a['kind']) for a in mine['agreements']], [('filed_past', 'services'), ('portal_signed', 'baa'), ('portal_signed', 'services')])
        past, baa, sa = mine['agreements']
        self.assertEqual((past['title'], past['reference'], past['signed_on'], past['effective_on'], past['completed_at']),
                         ('Services Agreement (DocuSign, 2025)', 'DS-4411-AB20', '2025-06-12', '2025-07-01', None))
        self.assertEqual(past['client_signers'], [{'name': 'Rebecca Moore', 'title': '', 'email': '', 'signed_at': None}])
        self.assertEqual([d['role'] for d in past['documents']], ['signed_copy'])
        self.assertEqual((baa['title'], baa['company'], baa['effective_on'], baa['governing_state'], baa['terms'], baa['services']),
                         ('Business Associate Agreement', {'key': 'scribe', 'legal_name': SCRIBE}, '2026-03-01', 'Nevada', {}, []))
        self.assertEqual(baa['client_signers'][0]['name'], 'Dr. Rebecca Moore, MD')
        self.assertEqual(baa['client_signers'][0]['title'], 'Owner')
        self.assertEqual(baa['company_signer'], {'name': 'Naeem Abbas', 'title': 'Chief Executive Officer'})
        self.assertEqual([d['role'] for d in baa['documents']], ['signed_copy', 'certificate'])
        self.assertEqual(baa['documents'][1], sa['documents'][1], 'one certificate for the envelope, named on both agreements')
        self.assertEqual(sa['terms'], {'notice_days': 30, 'payment_days': 15, 'cure_days': 10, 'special_terms': ''})
        billing, credentialing, custom = sa['services']
        self.assertEqual([o['letter'] for o in billing['options']], ['A', 'B'])
        self.assertEqual(billing['selected_option'], 'B')
        self.assertEqual(billing['pricing'], billing['options'][1]['pricing'], 'the chosen option is the price')
        self.assertEqual(billing['pricing'], {'model': 'fte', 'summary': billing['pricing']['summary'], 'rate': '1400.00', 'rate_unit': 'per_month', 'quantity': '1', 'weekly_hours': '40'})
        self.assertEqual(billing['options'][0]['pricing']['rate'], '5')
        self.assertEqual((credentialing['service_id'], credentialing['pricing']['government_rate'], credentialing['pricing']['commercial_rate'], credentialing['selected_option']),
                         ('credentialing', '150.00', '200.00', None))
        self.assertNotIn('options', credentialing)
        self.assertTrue(custom['is_custom'])
        self.assertRegex(custom['service_id'], r'^custom_[a-f0-9]{10}$')
        self.assertEqual((custom['name'], custom['pricing']['model'], custom['pricing']['rate']), ('Practice Launch Support', 'fixed', '750.00'))

    def test_price_wording_is_the_agreement_wording(self):
        """pricing.summary comes from the function that prints the price on the agreement."""
        import revised_content
        cid = self.client()
        payload = self.services_payload()
        self.signed_envelope(cid, payload=payload, option='Option A')
        feed.publish_once()
        sa = next(a for a in self.latest()[cid]['agreements'] if a['kind'] == 'services')
        rows = {tuple(r) for s in payload['services'] + payload['custom_services'] for r in revised_content.fee_rows(revised_content.original._service_record(s))}
        printed = {text for _, text in rows}
        for s in sa['services']:
            for p in [s['pricing']] + [o['pricing'] for o in s.get('options', [])]:
                self.assertIn(p['summary'], printed)
        blocks = legacy.build_documents(payload, 'SA-TEST')[1]['blocks']
        document = ' '.join(str(b.get('text', '')) + ' ' + str(b.get('rows', '')) for b in blocks)
        self.assertIn(sa['services'][0]['options'][1]['pricing']['summary'], document)

    # ------------------------------------------------------------------ hashes and publishing rules
    def test_hashes_are_exactly_the_contract_definitions_and_an_unchanged_client_is_not_published_again(self):
        a, b = self.client(), self.client(name='Lakeside Pediatrics LLC')
        self.signed_envelope(a)
        self.past_agreement(b)
        self.assertEqual(feed.publish_once(), 2)
        for ch in self.everything():
            s = ch['snapshot']
            body = {k: v for k, v in s.items() if k not in ('snapshot_hash', 'generated_at')}
            self.assertEqual(s['snapshot_hash'], hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')).hexdigest())
            self.assertFalse(list(V['client-snapshot'].iter_errors(s)))
            self.assertEqual(self.get(f"/clients/{ch['client_ref']}").json(), s)
        meta = self.get('/meta').json()
        self.assertFalse(list(V['meta'].iter_errors(meta)))
        pairs = sorted((ch['client_ref'], ch['snapshot']['snapshot_hash']) for ch in self.everything())
        self.assertEqual(meta['state_digest'], hashlib.sha256('\n'.join(f'{r}:{h}' for r, h in pairs).encode('utf-8')).hexdigest())
        self.assertEqual((meta['published_clients'], meta['head_seq'], meta['contract_revision'], meta['agreements_release']), (2, 2, 1, '5.3'))
        # nothing changed: a normal run, a full re-sweep and "publish everything again" publish nothing
        time.sleep(1.1)   # generated_at moves on; the hash must not
        self.assertEqual(feed.publish_once(), 0)
        self.assertEqual(feed.publish_once(force_sweep=True), 0)
        self.assertEqual(self.post('/api/admin/integrations/republish').status_code, 200)
        self.assertEqual(feed.publish_once(), 0)
        self.assertEqual(self.get('/meta').json()['head_seq'], 2)
        # a change is found by reading the tables (no hook): a new contact
        with db.engine.begin() as c:
            c.execute(t.client_contacts.insert().values(id=db.uid(), client_id=a, name='Sam Carter', email='sam@northsidefamily.test', title='Compliance', phone='',
                                                        role='compliance', notes='', created=time.time(), updated=time.time(), created_by=None))
        self.assertEqual(feed.publish_once(), 1)
        self.assertEqual(self.latest()[a]['contacts'][-1]['name'], 'Sam Carter')

    def test_test_clients_test_envelopes_and_unsigned_envelopes_never_appear(self):
        real = self.client()
        practice = self.client(name='Practice Run Clinic', test=True)
        self.signed_envelope(practice)
        quiet = self.client(name='Quiet Clinic LLC')
        self.signed_envelope(quiet, test=True)
        self.signed_envelope(quiet, contract_test=True)
        for status in ('sent', 'voided', 'declined', 'expired', 'draft'):
            self.signed_envelope(quiet, status=status)
        self.past_agreement(quiet, test=True)
        self.past_agreement(quiet, signed_on=None, with_file=False)   # filed without a date or a copy: not a signed agreement
        self.signed_envelope(real)
        feed.publish_once()
        self.assertEqual(set(self.latest()), {real})
        self.assertTrue(all(ch['snapshot']['is_test'] is False for ch in self.everything()))
        self.assertEqual(self.get(f'/clients/{practice}').status_code, 404)

    def test_marking_the_only_agreement_as_a_test_withdraws_the_client(self):
        cid = self.client()
        env_id, _ = self.signed_envelope(cid)
        feed.publish_once()
        self.assertIn(cid, self.latest())
        with db.engine.begin() as c:
            self.assertEqual(marks.mark_envelopes(c, dict(self.super), [env_id], True), 1)
        self.assertEqual(feed.publish_once(), 1, 'found through the activity log: marking does not touch an updated column')
        last = self.everything()[-1]
        self.assertEqual((last['kind'], last['client_ref']), ('client.withdrawn', cid))
        self.assertNotIn('snapshot', last)
        self.assertEqual(self.get(f'/clients/{cid}').status_code, 404)
        meta = self.get('/meta').json()
        self.assertEqual((meta['published_clients'], meta['state_digest']), (0, feed.state_digest([])))
        with db.engine.begin() as c:
            marks.mark_envelopes(c, dict(self.super), [env_id], False)
        self.assertEqual(feed.publish_once(), 1)
        self.assertIn(cid, self.latest())

    def test_trash_and_purge(self):
        cid = self.client()
        self.signed_envelope(cid)
        self.past_agreement(cid)
        feed.publish_once()
        with db.engine.begin() as c:
            client_trash.delete_clients(c, dict(self.super), [cid])
        self.assertEqual(feed.publish_once(), 1)
        s = self.latest()[cid]
        self.assertEqual((s['state'], s['legal_name']), ('trashed', 'Northside Family Clinic, PLLC'))
        with db.engine.begin() as c:
            self.assertEqual(client_trash.purge_due(c, now=time.time() + 31 * 86400), 1)
        self.assertEqual(feed.publish_once(), 1)
        s = self.latest()[cid]
        self.assertEqual((s['state'], s['legal_name'], s['contacts'], s['address'], s['email']), ('purged', f"{s['client_number']} (details removed)", [], '', ''))
        self.assertEqual(len(s['agreements']), 3, 'signed agreements are kept')
        self.assertFalse(list(V['client-snapshot'].iter_errors(s)))
        past = next(a for a in s['agreements'] if a['origin'] == 'filed_past')
        r = self.get(f"/documents/{past['documents'][0]['document_ref']}")
        self.assertEqual(r.status_code, 200, 'the signed copy of a past agreement survives the purge')

    def test_no_gaps_and_strict_order_under_20_concurrent_signings(self):
        ready = [self.client(name=f'Concurrent Clinic {i:02d} LLC') for i in range(20)]
        stop, errors = threading.Event(), []

        def publisher():
            while not stop.is_set():
                try:
                    feed.publish_once()
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

        def sign(cid, gate):
            gate.wait(timeout=30)
            try:
                self.signed_envelope(cid)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
        gate = threading.Barrier(20)
        pubs = [threading.Thread(target=publisher) for _ in range(3)]
        signers = [threading.Thread(target=sign, args=(cid, gate)) for cid in ready]
        [x.start() for x in pubs + signers]
        [x.join() for x in signers]
        time.sleep(0.5)
        stop.set()
        [x.join() for x in pubs]
        feed.publish_once()
        self.assertEqual(errors, [])
        changes = self.everything()
        self.assertEqual([ch['seq'] for ch in changes], list(range(1, len(changes) + 1)), 'no gaps, strictly increasing')
        self.assertEqual(set(self.latest()), set(ready))
        self.assertEqual(len(changes), 20, 'each client once: an unchanged picture is never published twice')

    # ------------------------------------------------------------------ access
    def test_tokens_read_only_revocation_and_limits(self):
        cid = self.client()
        self.signed_envelope(cid)
        feed.publish_once()
        self.assertEqual(self.get('/meta', token='').status_code, 401)
        self.assertEqual(self.get('/meta', token='not-a-real-token').json()['error'], 'unauthorized')
        for path in ('/meta', '/changes', f'/clients/{cid}', '/catalog'):
            self.assertIn(self.feed_api.post(FEED + path, json={}, headers={'Authorization': f'Bearer {self.token}'}).status_code, (401, 403, 404, 405))
            self.assertIn(self.feed_api.delete(FEED + path, headers={'Authorization': f'Bearer {self.token}'}).status_code, (401, 403, 404, 405))
        for limit in (0, 201, -1, 'x'):
            r = self.get('/changes', after=0, limit=limit)
            self.assertEqual(r.status_code, 400)
            self.assertEqual(set(r.json()), {'error', 'message'})
        self.assertEqual(self.get('/changes', after='-1').status_code, 400)
        self.assertEqual(len(self.get('/changes', after=0, limit=1).json()['changes']), 1)
        catalog = self.get('/catalog').json()
        self.assertFalse(list(V['catalog'].iter_errors(catalog)))
        self.assertIn({'key': 'scribe', 'legal_name': SCRIBE}, [{k: x[k] for k in ('key', 'legal_name')} for x in catalog['companies']])
        self.assertIn('medical_billing', [s['service_id'] for s in catalog['services']])
        # the feed answers over the private network under another host name (OCS on the same server)
        r = self.feed_api.get(FEED + '/meta', headers={'Authorization': f'Bearer {self.token}', 'Host': 'agreements:8000'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.feed_api.get('/api/clients', headers={'Host': 'agreements:8000'}).status_code, 403, 'only the feed: the rest of the application keeps its host check')
        # documents: announced size and hash; others 404
        snap = self.latest()[cid]
        doc = snap['agreements'][0]['documents'][0]
        r = self.get(f"/documents/{doc['document_ref']}")
        self.assertEqual((r.status_code, len(r.content), hashlib.sha256(r.content).hexdigest(), r.headers['x-content-sha256'], r.headers['content-length']),
                         (200, doc['size'], doc['sha256'], doc['sha256'], str(doc['size'])))
        self.assertEqual(self.get('/documents/d-' + 'a' * 32).status_code, 404)
        self.assertEqual(self.get('/documents/../../etc/passwd').status_code, 404)
        self.assertEqual(self.get('/clients/not-a-client-ref-000').status_code, 404)
        # revoke: 401 at once; the Admin screen shows it
        tokens = self.api.get('/api/admin/integrations').json()['tokens']
        self.assertEqual(self.post(f"/api/admin/integrations/tokens/{tokens[0]['id']}/revoke").status_code, 200)
        self.assertEqual(self.get('/meta').status_code, 401)
        view = self.api.get('/api/admin/integrations').json()
        self.assertTrue(view['tokens'][0]['revoked'])
        self.assertNotIn('token_hash', json.dumps(view))
        # failed attempts are limited per address
        codes = [self.get('/meta', token='wrong').status_code for _ in range(feed.FAILED_LIMIT + 2)]
        self.assertEqual(codes[-1], 429)
        self.assertEqual(self.get('/meta', token='wrong').json()['error'], 'too_many_requests')

    def test_only_the_super_admin_manages_tokens_and_the_token_is_stored_as_a_hash(self):
        for user in (self.admin, self.u1):
            self.login(user)
            self.assertEqual(self.api.get('/api/admin/integrations').status_code, 403)
            self.assertEqual(self.post('/api/admin/integrations/tokens', {'name': 'x'}).status_code, 403)
        with db.engine.connect() as c:
            stored = c.execute(select(feed.tokens.c.token_hash)).scalars().all()
            audit = [dict(r) for r in c.execute(select(db.audit)).mappings()]
        self.assertEqual(stored, [hashlib.sha256(self.token.encode()).hexdigest()])
        self.assertNotIn(self.token, json.dumps(audit, default=str))

    def test_without_a_token_nothing_runs_and_nothing_is_published(self):
        with db.engine.begin() as c:
            c.execute(feed.tokens.delete())
        cid = self.client()
        self.signed_envelope(cid)
        self.assertEqual(feed.publish_once(), 0)
        with db.engine.connect() as c:
            self.assertEqual(c.execute(select(func.count()).select_from(feed.feed)).scalar(), 0)
            self.assertEqual(feed.health_items(c, time.time()), [], 'the System check looks as in 5.2')
        self.assertEqual(self.feed_api.get(FEED + '/meta', headers={'Authorization': 'Bearer anything'}).status_code, 401)
        self.assertEqual(self.feed_api.get(FEED + '/meta').status_code, 401)

    def test_health_line_and_admin_view(self):
        cid = self.client()
        self.signed_envelope(cid)
        feed.publish_once()
        self.login(self.super)
        items = {i['key']: i for i in self.api.get('/api/system-status?fresh=1').json()['items']}
        self.assertEqual(items['integration']['state'], 'ok', items['integration'])
        self.assertIn('last published', items['integration']['detail'].lower())
        view = self.api.get('/api/admin/integrations').json()
        self.assertEqual((view['head_seq'], view['published_clients'], view['enabled']), (1, 1, True))
        with db.engine.begin() as c:
            c.execute(db.settings.update().where(db.settings.c.key == feed.SETTING_WATERMARK).values(value=time.time() - 7200))
        items = {i['key']: i for i in self.api.get('/api/system-status?fresh=1').json()['items']}
        self.assertEqual(items['integration']['state'], 'bad')

    def test_retention_keeps_each_clients_newest_change(self):
        a = self.client()
        self.signed_envelope(a)
        feed.publish_once()
        with db.engine.begin() as c:
            c.execute(update(db.clients).where(db.clients.c.id == a).values(address='1 New Street, Reno, NV 89501', updated=time.time()))
        feed.publish_once()
        import datetime as dt
        with db.engine.begin() as c:
            c.execute(feed.feed.update().values(published_at=feed.utcnow() - dt.timedelta(days=40)))
            self.assertEqual(feed.retention(c, feed.utcnow()), 1)
        changes = self.everything()
        self.assertEqual([ch['seq'] for ch in changes], [2])
        self.assertEqual(self.get('/meta').json()['head_seq'], 2)

    # ------------------------------------------------------------------ the real signing flow
    def test_a_real_signing_reaches_the_feed_with_its_signed_copies(self):
        self.connect_mailbox()
        contract_id, client_id = self.contract(self.u1)
        with db.engine.begin() as c:
            client_numbers.assign_one(c, client_id)
        self.login(self.u1)
        env = self.post('/api/esign/envelopes', {'contract_id': contract_id}).json()
        fields = [dict(f, recipient=f['recipient_id']) for f in env['fields']]
        people = [dict(r, name='Jane Example') if r['role_name'] == 'Client' else r for r in env['recipients']]
        self.assertEqual(self.post(f"/api/esign/envelopes/{env['id']}/draft", {'version': env['version'], 'recipients': people, 'fields': fields}).status_code, 200)
        self.assertEqual(self.post(f"/api/esign/envelopes/{env['id']}/send").status_code, 200)
        self.drain()
        self.assertEqual(feed.publish_once(), 0, 'out for signature: not published')
        for address in [r['email'] for r in sorted(people, key=lambda r: r['routing_order'])]:
            who, base, state = self.signer(self.smtp.link(self.smtp.to(address)[0]))
            self.assertEqual(self.complete_signing(who, base, state).status_code, 200)
            self.drain()
        self.assertEqual(feed.publish_once(), 1)
        snap = self.latest()[client_id]
        self.assertFalse(list(V['client-snapshot'].iter_errors(snap)))
        self.assertEqual([a['kind'] for a in snap['agreements']], ['baa', 'services'])
        sa = snap['agreements'][1]
        self.assertEqual((sa['reference'], sa['client_signers'][0]['name'], sa['client_signers'][0]['title']), (contract_id, 'Jane Example', 'Managing Member'))
        self.assertEqual(sa['services'][0]['service_id'], 'credentialing')
        self.assertEqual(sa['services'][0]['pricing']['model'], 'two_installments')
        for d in sa['documents']:
            r = self.get(f"/documents/{d['document_ref']}")
            self.assertEqual((r.status_code, hashlib.sha256(r.content).hexdigest(), len(r.content)), (200, d['sha256'], d['size']))


class Contract(Base):
    def test_contract_is_additive(self):
        """The schemas equal the last released copy or only add to it (04 §2)."""
        def walk(old, new, where):
            if isinstance(old, dict):
                self.assertIsInstance(new, dict, where)
                for k, v in old.items():
                    if k in ('description', 'title'):
                        continue
                    self.assertIn(k, new, f'{where}/{k} was removed')
                    if k == 'required':
                        self.assertTrue(set(new[k]) <= set(v), f'{where}: a property became required')
                    elif k in ('enum',):
                        self.assertTrue(set(v) <= set(new[k]), f'{where}: an enum value was removed')
                    else:
                        walk(v, new[k], f'{where}/{k}')
            elif isinstance(old, list) and where.endswith('/type'):
                self.assertTrue(set(old) <= set(new if isinstance(new, list) else [new]), where)
            else:
                self.assertEqual(old, new, where)
        released = CONTRACT_DIR / 'released'
        self.assertTrue(list(released.glob('*.schema.json')))
        for old in released.glob('*.schema.json'):
            walk(json.loads(old.read_text()), json.loads((CONTRACT_DIR / old.name).read_text()), old.name)

    def test_the_example_validates_and_the_hash_functions_match_the_contract(self):
        example = json.loads((CONTRACT_DIR / 'examples' / 'changes-page.example.json').read_text())
        self.assertFalse(list(V['changes'].iter_errors(example)))
        snap = copy.deepcopy(example['changes'][0]['snapshot'])
        body = {k: v for k, v in snap.items() if k not in ('snapshot_hash', 'generated_at')}
        self.assertEqual(feed.snapshot_hash(snap), hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest())
        snap['generated_at'] = '2030-01-01T00:00:00Z'
        self.assertEqual(feed.snapshot_hash(snap), feed.snapshot_hash(example['changes'][0]['snapshot']))
        self.assertEqual(feed.state_digest([('b', '2'), ('a', '1')]), hashlib.sha256(b'a:1\nb:2').hexdigest())
        self.assertEqual(feed.state_digest([]), hashlib.sha256(b'').hexdigest())

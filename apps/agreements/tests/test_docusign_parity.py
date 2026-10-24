"""DocuSign-style features added to e-signature: branding, field types and logic, finish later, reassignment, in-person
signing, corrections in flight, signer attachments, uploads in other formats, bulk send, reports and client profiles.
Each feature is also checked from the side of someone who must not be able to use it."""
import base64
import csv
import io
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pypdf import PdfReader
from sqlalchemy import select

import database as db
import esign_db as t
from esign_support import agreement_pdf, signature_png
from test_esign import EHS, SCRIBE, Base

APP = Path(__file__).resolve().parents[1]


def png(width=40, height=20) -> str:
    from PIL import Image
    out = io.BytesIO()
    Image.new('RGB', (width, height), (20, 60, 140)).save(out, format='PNG')
    return base64.b64encode(out.getvalue()).decode()


class Parity(Base):
    def draft(self, owner=None, entity=SCRIBE, client_id=None):
        """A draft with one uploaded two-page PDF, owned by owner (default: the Scribe Align user)."""
        owner = owner or self.u1
        self.login(owner)
        r = self.post('/api/esign/envelopes', {'entity': entity, 'client_id': client_id})
        self.assertEqual(r.status_code, 200, r.text)
        env = r.json()
        r = self.post(f"/api/esign/envelopes/{env['id']}/documents", {'name': 'Order form.pdf', 'data': base64.b64encode(agreement_pdf()).decode()})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def save(self, env, recipients, fields, **extra):
        r = self.post(f"/api/esign/envelopes/{env['id']}/draft", {'version': env['version'], 'recipients': recipients, 'fields': fields} | extra)
        return r

    def box(self, doc, page=1, x=60, y=100, w=150, h=18, **kw):
        return {'document_id': doc, 'page': page, 'x': x, 'y': y, 'w': w, 'h': h} | kw

    def send(self, env):
        r = self.post(f"/api/esign/envelopes/{env['id']}/send")
        self.assertEqual(r.status_code, 200, r.text)
        self.drain()
        return r.json()['envelope']

    def detail(self, env_id):
        return self.api.get(f'/api/esign/envelopes/{env_id}').json()

    # ------------------------------------------------------------------ branding
    def test_signing_email_carries_the_company_logo_inline_and_no_double_period(self):
        self.connect_mailbox(entity=EHS, address='compliance@ehs.test')
        env = self.draft(owner=self.ehs_user, entity=EHS)
        doc = env['documents'][0]['id']
        r = self.save(env, [{'key': 'a', 'role': 'signer', 'routing_order': 1, 'name': 'Pat Doctor', 'email': 'pat@clinic.test'}],
                      [self.box(doc, recipient='a', kind='signature', w=170, h=40)], message='Please sign the agreement from EXTENDED HEALTH SERVICES INC.')
        self.assertEqual(r.status_code, 200, r.text)
        self.send(r.json())
        msg = self.smtp.to('pat@clinic.test')[0]['msg']
        html = msg.get_body(preferencelist=('html',)).get_content()
        self.assertIn('cid:logo.ehs@tfsc', html)
        self.assertIn('REVIEW DOCUMENT', html)
        self.assertIn('#1C2A39', html, 'the banner uses the company colour (from extendedhealthservices.us)')
        self.assertNotIn('INC..', html.upper())
        images = {p.get('Content-ID'): p.get_content_type() for p in msg.walk() if p.get('Content-ID')}
        self.assertEqual(images, {'<logo.ehs@tfsc>': 'image/png', '<doc.ehs@tfsc>': 'image/png'})
        logo = next(p for p in msg.walk() if p.get('Content-ID') == '<logo.ehs@tfsc>').get_payload(decode=True)
        self.assertEqual(logo, (APP / 'assets' / 'brand' / 'ehs.png').read_bytes())
        _, base, state = self.signer(self.smtp.link(msg and self.smtp.to('pat@clinic.test')[0]))
        self.assertEqual(state['brand'], {'logo': '/assets/brand/ehs.png', 'color': '#1C2A39', 'accent': '#FBCD35'})
        self.assertEqual(self.api.get('/assets/brand/ehs.png').status_code, 200, 'logos are public so emails and the signing page can show them')
        self.assertEqual(self.api.get('/assets/brand/../../portal.py').status_code, 404)

    # ------------------------------------------------------------------ field types, validation and conditional logic
    def test_field_types_validation_and_conditional_logic_are_enforced_by_the_server(self):
        self.connect_mailbox()
        env = self.draft()
        doc = env['documents'][0]['id']
        fields = [self.box(doc, recipient='a', kind='signature', w=170, h=40, y=40),
                  self.box(doc, recipient='a', kind='first_name', y=100), self.box(doc, recipient='a', kind='last_name', y=130),
                  self.box(doc, recipient='a', kind='text', y=160, label='Phone', options={'validation': 'phone'}),
                  self.box(doc, recipient='a', kind='number', y=190, label='Providers'),
                  self.box(doc, recipient='a', kind='date', y=220, label='Start date'),
                  self.box(doc, recipient='a', kind='text', y=250, label='Plan code', options={'readonly': True, 'prefill': 'SA-2026'}),
                  self.box(doc, recipient='a', kind='checkbox', y=280, w=14, h=14, key='cb', label='Has billing contact', required=False),
                  self.box(doc, recipient='a', kind='text', y=300, key='dep', label='Billing contact', options={'condition': {'field': 'cb', 'equals': 'checked'}}),
                  self.box(doc, recipient='a', kind='radio', y=330, w=13, h=13, key='r1', options={'group': 'Plan', 'choice': 'Monthly'}, required=True),
                  self.box(doc, recipient='a', kind='radio', y=350, w=13, h=13, key='r2', options={'group': 'Plan', 'choice': 'Annual'}, required=True),
                  self.box(doc, recipient='a', kind='text', y=380, label='Annual PO', options={'condition': {'field': 'r2', 'equals': 'Annual'}}),
                  self.box(doc, recipient='a', kind='note', y=420, w=200, h=40, options={'text': 'Call us with questions.'})]
        people = [{'key': 'a', 'role': 'signer', 'routing_order': 1, 'name': 'Jane Q Example', 'email': 'jane@client.test'}]
        bad = self.save(env, people, fields[:1] + [self.box(doc, recipient='a', kind='text', key='x', options={'condition': {'field': 'x', 'equals': 'checked'}})])
        self.assertEqual(bad.status_code, 400, 'a field cannot depend on itself')
        bad = self.save(env, people, fields[:1] + [self.box(doc, recipient='a', kind='text', options={'readonly': True})])
        self.assertEqual(bad.status_code, 400, 'read-only fields need a value')
        r = self.save(env, people, fields)
        self.assertEqual(r.status_code, 200, r.text)
        env = r.json()
        dep = next(f for f in env['fields'] if f['label'] == 'Billing contact')
        cb = next(f for f in env['fields'] if f['kind'] == 'checkbox')
        self.assertEqual(dep['options']['condition'], {'field': cb['id'], 'equals': 'checked'}, 'editor keys are resolved to field ids')
        self.send(env)
        jane, base, state = self.signer(self.smtp.link(self.smtp.to('jane@client.test')[0]))
        self.sign_post(jane, base, 'consent', state)
        state = self.sign_post(jane, base, 'adopt', state, {'kind': 'drawn', 'signature': signature_png(), 'initials': signature_png('JE', (120, 60))}).json()
        by = {f['label'] or f['kind'] + (f['options'].get('choice') or ''): f['id'] for f in state['fields']}
        values = {by['signature']: True, by['Phone']: '555-0100', by['Providers']: '12', by['Start date']: '11/01/2026', by['radioAnnual']: True}
        r = self.sign_post(jane, base, 'finish', state, {'values': values})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'Phone: enter a 10-digit phone number.'))
        values[by['Phone']] = '(555) 010-0199'
        r = self.sign_post(jane, base, 'finish', state, {'values': values})
        self.assertEqual(r.status_code, 400, 'the Annual PO is required once Annual is chosen')
        values[by['Annual PO']] = 'PO-77'
        values[by['Plan code']] = 'changed by the signer'
        r = self.sign_post(jane, base, 'finish', state, {'values': values})
        self.assertEqual(r.status_code, 200, r.text)
        self.drain()
        with db.engine.connect() as c:
            stored = {f['label'] or f['kind'] + (f['options'].get('choice') or ''): f['value'] for f in c.execute(select(t.fields)).mappings()}
            final = c.execute(select(t.documents.c.final_pdf)).scalar()
        self.assertEqual((stored['first_name'], stored['last_name']), ('Jane', 'Example'))
        self.assertEqual(stored['Plan code'], 'SA-2026', 'read-only values cannot be changed by the signer')
        self.assertIsNone(stored['Billing contact'], 'a hidden conditional field stays empty')
        self.assertEqual((stored['radioMonthly'], stored['radioAnnual']), ('false', 'true'))
        self.assertIsNone(stored['note'], 'notes are shown while signing, never stamped')
        text = ''.join(p.extract_text() for p in PdfReader(io.BytesIO(final)).pages)
        for expected in ('Jane', 'Example', '(555) 010-0199', '12', '11/01/2026', 'SA-2026', 'PO-77'):
            self.assertIn(expected, text)
        self.assertNotIn('Call us with questions', text)

    # ------------------------------------------------------------------ finish later and reassignment
    def test_finish_later_keeps_entries_until_the_signer_returns(self):
        self.connect_mailbox()
        env = self.draft()
        doc = env['documents'][0]['id']
        r = self.save(env, [{'key': 'a', 'role': 'signer', 'routing_order': 1, 'name': 'Jane', 'email': 'jane@client.test'}],
                      [self.box(doc, recipient='a', kind='signature', w=170, h=40), self.box(doc, recipient='a', kind='text', y=200, label='Title')])
        self.send(r.json())
        link = self.smtp.link(self.smtp.to('jane@client.test')[0])
        jane, base, state = self.signer(link)
        self.assertEqual(self.sign_post(jane, base, 'save', state, {'values': {}}).status_code, 403, 'consent first')
        state = self.sign_post(jane, base, 'consent', state).json()
        title = next(f['id'] for f in state['fields'] if f['kind'] == 'text')
        r = self.sign_post(jane, base, 'save', state, {'values': {title: 'Owner', 'not-a-field': 'x'}})
        self.assertEqual(r.status_code, 200, r.text)
        jane2, base2, state2 = self.signer(link)
        self.assertEqual(state2['progress'], {title: 'Owner'}, 'the same link brings the saved entries back')
        self.assertTrue(self.detail(env['id'])['recipients'][0]['saved_progress'])
        r = self.complete_signing(jane2, base2, state2, {title: 'Owner'})
        self.assertEqual(r.status_code, 200, r.text)
        with db.engine.connect() as c:
            self.assertIsNone(c.execute(select(t.recipients.c.progress)).scalar(), 'saved progress is cleared once signed')

    def test_assign_to_someone_else_moves_the_fields_and_notifies(self):
        self.connect_mailbox()
        env = self.draft()
        doc = env['documents'][0]['id']
        r = self.save(env, [{'key': 'a', 'role': 'signer', 'routing_order': 1, 'name': 'Jane', 'email': 'jane@client.test', 'note': 'Thanks'}],
                      [self.box(doc, recipient='a', kind='signature', w=170, h=40)])
        self.send(r.json())
        link = self.smtp.link(self.smtp.to('jane@client.test')[0])
        jane, base, state = self.signer(link)
        self.assertTrue(state['allow_reassign'])
        r = self.sign_post(jane, base, 'reassign', state, {'name': 'Jane', 'email': 'jane@client.test', 'reason': 'x'})
        self.assertEqual(r.status_code, 400, 'reassigning to yourself is refused')
        r = self.sign_post(jane, base, 'reassign', state, {'name': 'Dr. Lee', 'email': 'lee@client.test', 'reason': 'Dr. Lee signs our contracts.'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['recipient']['status'], 'reassigned')
        self.drain()
        invite = self.smtp.to('lee@client.test')
        self.assertEqual(len(invite), 1)
        self.assertIn('Dr. Lee signs our contracts.', invite[0]['msg'].get_body(preferencelist=('plain',)).get_content())
        self.assertTrue(any(m['msg']['Subject'].startswith('Reassigned:') for m in self.smtp.to('sender@scribealign.test')))
        self.assertEqual(self.signer(link)[2]['recipient']['status'], 'reassigned', "the old link only says it was handed over")
        lee, base2, state2 = self.signer(self.smtp.link(invite[0]))
        self.assertEqual(self.complete_signing(lee, base2, state2).status_code, 200)
        self.drain()
        d = self.detail(env['id'])
        self.assertEqual(d['status'], 'completed')
        self.assertEqual([m for m in self.smtp.to('jane@client.test') if m['msg']['Subject'].startswith('Completed:')], [], 'the person who handed over gets no copy')

    def test_reassignment_can_be_turned_off(self):
        self.connect_mailbox()
        env = self.draft()
        doc = env['documents'][0]['id']
        r = self.save(env, [{'key': 'a', 'role': 'signer', 'routing_order': 1, 'name': 'Jane', 'email': 'jane@client.test'}],
                      [self.box(doc, recipient='a', kind='signature', w=170, h=40)], settings={'allow_reassign': False})
        self.send(r.json())
        jane, base, state = self.signer(self.smtp.link(self.smtp.to('jane@client.test')[0]))
        self.assertFalse(state['allow_reassign'])
        r = self.sign_post(jane, base, 'reassign', state, {'name': 'X', 'email': 'x@client.test', 'reason': 'x'})
        self.assertEqual(r.status_code, 403)

    # ------------------------------------------------------------------ in-person signing
    def test_in_person_signing_is_started_only_by_the_host(self):
        self.connect_mailbox()
        env = self.draft()
        doc = env['documents'][0]['id']
        r = self.save(env, [{'key': 'a', 'role': 'in_person', 'routing_order': 1, 'name': 'Walk-in Patient', 'email': '', 'host_id': self.u2['id']}],
                      [self.box(doc, recipient='a', kind='signature', w=170, h=40)])
        self.assertEqual(r.status_code, 200, r.text)
        bad = self.save(r.json(), [{'key': 'a', 'role': 'in_person', 'routing_order': 1, 'name': 'X', 'host_id': self.ehs_user['id']}], [])
        self.assertEqual(bad.status_code, 400, 'the host must work for the sending company')
        env = self.send(r.json())
        self.assertEqual([m['msg']['Subject'] for m in self.smtp.to(self.u2['email'])], [f"Host in-person signing: {env['subject']}"])
        rid = env['recipients'][0]['id']
        self.login(self.admin)
        self.assertEqual(self.post(f"/api/esign/envelopes/{env['id']}/host/{rid}").status_code, 404, 'other users cannot start it')
        self.login(self.u2)
        self.assertEqual(self.api.get('/api/esign/envelopes?box=action').json()['counts']['action'], 1, 'the host sees it under Action required')
        r = self.post(f"/api/esign/envelopes/{env['id']}/host/{rid}")
        self.assertEqual(r.status_code, 200, r.text)
        token = r.json()['url'].rsplit('/', 1)[1]
        walk, base, state = self.signer(token)
        self.assertTrue(state['in_person'] and state['can_act'])
        self.assertEqual(self.complete_signing(walk, base, state).status_code, 200)
        self.drain()
        d = self.detail(env['id'])
        self.assertEqual(d['status'], 'completed', 'the host can follow the envelope they hosted')
        self.assertEqual(self.post(f"/api/esign/envelopes/{env['id']}/copy").status_code, 404, 'hosting gives no sender rights')
        self.assertIn('In person, hosted by', d['recipients'][0]['auth_method'])
        self.assertIn(self.u2['email'], d['recipients'][0]['auth_method'])

    # ------------------------------------------------------------------ correcting an envelope in flight
    def test_correct_recipients_and_fields_while_out_for_signature(self):
        self.connect_mailbox()
        env = self.draft()
        doc = env['documents'][0]['id']
        people = [{'key': 'a', 'role': 'signer', 'routing_order': 1, 'name': 'Jane', 'email': 'jane@client.test'},
                  {'key': 'b', 'role': 'signer', 'routing_order': 2, 'name': 'Bob', 'email': 'bob@wrong.test'}]
        r = self.save(env, people, [self.box(doc, recipient='a', kind='signature', w=170, h=40), self.box(doc, recipient='b', kind='signature', w=170, h=40, y=300)])
        env = self.send(r.json())
        jane, base, state = self.signer(self.smtp.link(self.smtp.to('jane@client.test')[0]))
        self.assertEqual(self.complete_signing(jane, base, state).status_code, 200)
        self.drain()
        env = self.detail(env['id'])
        jane_r, bob_r = env['recipients']
        bob_fields = [dict(f, recipient=f['recipient_id']) for f in env['fields'] if f['recipient_id'] == bob_r['id']]
        layout = {'recipients': [{'id': jane_r['id']}, dict(bob_r, email='bob@client.test'),
                                 {'key': 'c', 'role': 'signer', 'routing_order': 3, 'name': 'Carol', 'email': 'carol@client.test'},
                                 {'key': 'd', 'role': 'cc', 'routing_order': 1, 'name': 'Office', 'email': 'office@client.test'}],
                  'fields': bob_fields + [self.box(doc, recipient='c', kind='signature', w=170, h=40, y=500)]}
        self.login(self.u1)
        r = self.post(f"/api/esign/envelopes/{env['id']}/correct", {'layout': {'recipients': layout['recipients'][1:], 'fields': layout['fields']}})
        self.assertEqual(r.status_code, 400, 'someone who already signed cannot be removed')
        r = self.post(f"/api/esign/envelopes/{env['id']}/correct", {'layout': layout})
        self.assertEqual(r.status_code, 200, r.text)
        self.drain()
        self.assertEqual(len(self.smtp.to('bob@client.test')), 1, 'the corrected address gets the envelope')
        self.assertEqual(len(self.smtp.to('office@client.test')), 1, 'a new copy recipient whose turn has come gets it now')
        self.assertEqual(self.smtp.to('carol@client.test'), [], 'a new signer waits for their turn')
        d = self.detail(env['id'])
        self.assertEqual(d['recipients'][0]['status'], 'signed', 'the person who signed is untouched')
        self.assertEqual(sum(f['recipient_id'] == jane_r['id'] for f in d['fields']), 1)
        bob, base, state = self.signer(self.smtp.link(self.smtp.to('bob@client.test')[0]))
        self.assertEqual(self.complete_signing(bob, base, state).status_code, 200)
        self.drain()
        carol, base, state = self.signer(self.smtp.link(self.smtp.to('carol@client.test')[0]))
        self.assertEqual(self.complete_signing(carol, base, state).status_code, 200)
        self.drain()
        self.assertEqual(self.detail(env['id'])['status'], 'completed')

    # ------------------------------------------------------------------ signer attachments
    def test_signer_attachment_is_scanned_converted_and_delivered_with_the_documents(self):
        self.connect_mailbox()
        env = self.draft()
        doc = env['documents'][0]['id']
        r = self.save(env, [{'key': 'a', 'role': 'signer', 'routing_order': 1, 'name': 'Jane', 'email': 'jane@client.test'}],
                      [self.box(doc, recipient='a', kind='signature', w=170, h=40), self.box(doc, recipient='a', kind='attachment', y=200, w=130, h=26, label='W-9')])
        env = self.send(r.json())
        jane, base, state = self.signer(self.smtp.link(self.smtp.to('jane@client.test')[0]))
        state = self.sign_post(jane, base, 'consent', state).json()
        field = next(f for f in state['fields'] if f['kind'] == 'attachment')
        r = self.sign_post(jane, base, 'attachment', state, {'field_id': field['id'], 'name': 'w9.docx', 'data': base64.b64encode(b'PK\x03\x04' + b'0' * 200).decode()})
        self.assertEqual(r.status_code, 400, 'signers attach PDFs or photos only')
        r = self.sign_post(jane, base, 'attachment', state, {'field_id': field['id'], 'name': 'w9 photo.png', 'data': png(400, 300)})
        self.assertEqual(r.status_code, 200, r.text)
        field = next(f for f in r.json()['fields'] if f['kind'] == 'attachment')
        self.assertTrue(field['attachment_name'].startswith('W-9 - Jane - w9 photo'))
        r = self.complete_signing(jane, base, r.json(), {field['id']: field['value']})
        self.assertEqual(r.status_code, 200, r.text)
        self.drain()
        d = self.detail(env['id'])
        self.assertEqual([x['source'] for x in d['documents']], ['upload', 'attachment'])
        done = [m for m in self.smtp.to('jane@client.test') if m['msg']['Subject'].startswith('Completed:')][0]
        names = [a.get_filename() for a in done['msg'].iter_attachments()]
        self.assertEqual(len(names), 3, names)
        history = [h['kind'] for h in d['history']]
        self.assertIn('attachment', history)

    # ------------------------------------------------------------------ uploads in other formats
    def test_images_and_text_files_are_converted_to_pdf(self):
        env = self.draft()
        r = self.post(f"/api/esign/envelopes/{env['id']}/documents", {'name': 'id card.png', 'data': png(600, 380)})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['documents'][-1]['name'], 'id card.pdf')
        r = self.post(f"/api/esign/envelopes/{env['id']}/documents", {'name': 'evil.exe', 'data': base64.b64encode(b'MZ' + b'0' * 300).decode()})
        self.assertEqual(r.status_code, 400)
        r = self.post(f"/api/esign/envelopes/{env['id']}/documents", {'name': 'renamed.docx', 'data': base64.b64encode(b'%PDF-1.4 not really').decode()})
        self.assertEqual(r.status_code, 400, 'content decides the type, and broken PDFs are refused')

    @unittest.skipUnless(shutil.which('soffice'), 'LibreOffice is needed to convert Word documents')
    def test_word_documents_are_converted_with_libreoffice(self):
        env = self.draft()
        raw = (APP / 'assets' / 'letterhead.docx').read_bytes()
        r = self.post(f"/api/esign/envelopes/{env['id']}/documents", {'name': 'Proposal.docx', 'data': base64.b64encode(raw).decode()})
        self.assertEqual(r.status_code, 200, r.text)
        added = r.json()['documents'][-1]
        self.assertEqual((added['name'], added['pages'] >= 1), ('Proposal.pdf', True))

    # ------------------------------------------------------------------ bulk send and reports
    def test_bulk_send_creates_one_envelope_per_person_and_links_clients(self):
        self.connect_mailbox()
        env = self.draft()
        doc = env['documents'][0]['id']
        r = self.save(env, [{'key': 'a', 'role': 'signer', 'routing_order': 1, 'name': 'Placeholder', 'email': 'placeholder@client.test'},
                            {'key': 'b', 'role': 'cc', 'routing_order': 2, 'name': 'Compliance', 'email': 'cc@scribealign.test'}],
                      [self.box(doc, recipient='a', kind='signature', w=170, h=40)], subject='Please sign, {{first_name}}', message='Hello {{name}} at {{client}}')
        self.assertEqual(r.status_code, 200, r.text)
        r = self.post(f"/api/esign/envelopes/{env['id']}/copy", {'template_name': 'Order form'})
        template = r.json()
        rows = [{'name': 'Ann Alpha', 'email': 'ann@a.test', 'client': 'Alpha Clinic'}, {'name': 'Ben Beta', 'email': 'ben@b.test'}]
        self.assertEqual(self.post('/api/esign/bulk', {'template_id': template['id'], 'rows': rows + [rows[0]]}).status_code, 400, 'duplicates are refused')
        self.login(self.ehs_user)
        self.assertEqual(self.post('/api/esign/bulk', {'template_id': template['id'], 'rows': rows}).status_code, 404, 'other companies cannot use the template')
        self.login(self.u1)
        r = self.post('/api/esign/bulk', {'template_id': template['id'], 'rows': rows, 'name': 'Test batch'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()['sent'], r.json()['failed']), (2, 0))
        self.drain()
        ann = self.smtp.to('ann@a.test')[0]['msg']
        self.assertEqual(ann['Subject'], 'Please sign, Ann')
        self.assertIn('Hello Ann Alpha at Alpha Clinic', ann.get_body(preferencelist=('plain',)).get_content())
        self.assertEqual(self.smtp.to('placeholder@client.test'), [])
        clients = self.api.get('/api/clients').json()['clients']
        alpha = next(c for c in clients if c['client_name'] == 'Alpha Clinic')
        self.assertEqual(len(self.api.get(f"/api/clients/{alpha['id']}/envelopes").json()['envelopes']), 1)
        self.assertEqual(len(self.api.get('/api/esign/bulk').json()['batches']), 1)
        self.login(self.u2)
        self.assertEqual(self.api.get('/api/esign/bulk').json()['batches'], [], "another user does not see someone else's batches")

    def test_reports_count_what_the_user_may_see_and_export_safe_csv(self):
        self.connect_mailbox()
        env = self.draft()
        doc = env['documents'][0]['id']
        r = self.save(env, [{'key': 'a', 'role': 'signer', 'routing_order': 1, 'name': '=HYPERLINK("x")', 'email': 'jane@client.test'}],
                      [self.box(doc, recipient='a', kind='signature', w=170, h=40)], subject='=cmd|calc')
        self.send(r.json())
        report = self.api.get('/api/esign/reports').json()
        self.assertEqual((report['sent'], report['statuses']), (1, {'sent': 1}))
        self.assertEqual(report['by_company'][0]['name'], SCRIBE)
        body = self.api.get('/api/esign/reports/envelopes.csv').content.decode('utf-8-sig')
        row = list(csv.reader(io.StringIO(body)))[1]
        self.assertEqual(row[1], "'=cmd|calc", 'cells that start like a formula are neutralised')
        self.assertTrue(row[9].startswith("'="))
        self.login(self.u2)
        self.assertEqual(self.api.get('/api/esign/reports').json()['sent'], 0)
        self.login(self.admin)
        self.assertEqual(self.api.get('/api/esign/reports').json()['sent'], 1, "an Admin sees their team's envelopes")

    # ------------------------------------------------------------------ client profiles
    def test_client_profile_contacts_files_and_document_library(self):
        self.connect_mailbox()
        self.login(self.u1)
        r = self.post('/api/clients/new', {'name': 'Riverbend Family Clinic', 'address': '12 Oak St', 'phone': '555-0100', 'email': 'office@riverbend.test',
                                           'npi': '1234567890', 'entity': SCRIBE})
        self.assertEqual(r.status_code, 200, r.text)
        cid = r.json()['id']
        self.assertEqual(self.post('/api/clients/new', {'name': 'riverbend family clinic', 'entity': SCRIBE}).status_code, 409)
        self.assertEqual(self.post('/api/clients/new', {'name': 'Bad NPI', 'npi': '12', 'entity': SCRIBE}).status_code, 400)
        self.assertEqual(self.post('/api/clients/new', {'name': 'Wrong company', 'entity': EHS}).status_code, 403)
        r = self.post(f'/api/clients/{cid}/contacts', {'name': 'Pat Doctor', 'email': 'pat@riverbend.test', 'title': 'Owner', 'role': 'signatory'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.post(f'/api/clients/{cid}/contacts', {'name': 'Dup', 'email': 'PAT@riverbend.test'}).status_code, 409)
        self.assertEqual(self.api.get('/api/contacts?q=pat').json()['contacts'][0]['email'], 'pat@riverbend.test', 'contacts feed the address book')
        card = agreement_pdf()
        r = self.post(f'/api/clients/{cid}/files', {'name': 'Insurance card.pdf', 'data': base64.b64encode(card).decode(), 'category': 'insurance'})
        self.assertEqual(r.status_code, 200, r.text)
        fid = r.json()['id']
        self.assertEqual(self.post(f'/api/clients/{cid}/files', {'name': 'fake.pdf', 'data': base64.b64encode(b'<html>').decode()}).status_code, 400,
                         'a file must match its type')
        self.assertEqual(self.post(f'/api/clients/{cid}/files', {'name': 'run.exe', 'data': base64.b64encode(b'MZ').decode()}).status_code, 400)
        download = self.api.get(f'/api/clients/files/{fid}')
        self.assertEqual((download.status_code, download.content), (200, card))
        # Send the uploaded file for signature, sign it, and find the signed copy and certificate in the library.
        r = self.post(f'/api/clients/files/{fid}/send', {})
        self.assertEqual(r.status_code, 200, r.text)
        env = r.json()
        self.assertEqual(env['client_id'], cid)
        doc = env['documents'][0]['id']
        r = self.save(env, [{'key': 'a', 'role': 'signer', 'routing_order': 1, 'name': 'Pat Doctor', 'email': 'pat@riverbend.test'}],
                      [self.box(doc, recipient='a', kind='signature', w=170, h=40)])
        self.send(r.json())
        pat, base, state = self.signer(self.smtp.link(self.smtp.to('pat@riverbend.test')[0]))
        self.assertEqual(self.complete_signing(pat, base, state).status_code, 200)
        self.drain()
        library = self.api.get(f'/api/clients/{cid}/documents').json()['documents']
        groups = sorted({d['group'] for d in library})
        self.assertEqual(groups, ['Certificates of Completion', 'Signed documents', 'Uploaded files'])
        signed = next(d for d in library if d['group'] == 'Signed documents')
        self.assertEqual(self.api.get(signed['url']).content[:4], b'%PDF')
        profile = self.api.get(f'/api/clients/{cid}/profile').json()
        self.assertEqual(profile['counts']['envelopes'], {'completed': 1})
        self.assertEqual([c['email'] for c in profile['contacts']], ['pat@riverbend.test'], 'a recipient already in contacts is not added twice')
        # Someone else in the company cannot see or change this client.
        self.login(self.u2)
        self.assertEqual(self.api.get(f'/api/clients/{cid}/profile').status_code, 404)
        self.assertEqual(self.api.get(f'/api/clients/files/{fid}').status_code, 404)
        self.assertEqual(self.post(f'/api/clients/files/{fid}/delete').status_code, 404)
        self.assertEqual(self.api.get('/api/contacts?q=pat').json()['contacts'], [])
        # The owner's Admin can; only the uploader or an administrator removes a file.
        self.login(self.admin)
        self.assertEqual(self.api.get(f'/api/clients/{cid}/profile').status_code, 200)
        self.assertEqual(self.post(f'/api/clients/files/{fid}/delete').status_code, 200)

    def test_recipients_of_a_client_envelope_become_contacts_but_company_people_do_not(self):
        self.connect_mailbox()
        contract_id, client_id = self.contract(self.u1, include_signature=False)
        self.login(self.u1)
        env = self.post('/api/esign/envelopes', {'contract_id': contract_id}).json()
        self.send(env)
        contacts = self.api.get(f'/api/clients/{client_id}/profile').json()['contacts']
        self.assertEqual([c['email'] for c in contacts], ['jane@client.test'])

    def test_large_upload_limit_only_on_upload_paths(self):
        self.login(self.u1)
        big = {'name': 'x', 'data': 'A' * 3_000_000}
        self.assertEqual(self.post('/api/clients/new', big).status_code, 413)
        env = self.draft()
        r = self.post(f"/api/esign/envelopes/{env['id']}/documents", big)
        self.assertNotEqual(r.status_code, 413)


if __name__ == '__main__':
    unittest.main()

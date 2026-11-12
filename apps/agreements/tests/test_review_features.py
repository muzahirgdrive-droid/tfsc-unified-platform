"""The November 12 review: printing signed documents, standard margins, services & pricing, several fee options, agreement
types from Word files, follow-up notes, and the system check moved to Admin."""
import base64
import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import catalog_admin
import database as db
import doc_types
import entities
import esign_db as t
import server as legacy
from sqlalchemy import select

import test_esign
from test_contract_studio import data
from test_esign import SCRIBE

Base = test_esign.Base


def docx(paragraphs):
    """A minimal Word file: (style, text) pairs."""
    body = ''.join(f'<w:p>{f"<w:pPr><w:pStyle w:val={chr(34)}{s}{chr(34)}/></w:pPr>" if s else ""}<w:r><w:t xml:space="preserve">{x}</w:t></w:r></w:p>' for s, x in paragraphs)
    xml = f'<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>'
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr('word/document.xml', xml)
    return out.getvalue()


class Catalog(Base):
    def setUp(self):
        super().setUp()
        catalog_admin.invalidate()

    def tearDown(self):
        catalog_admin.invalidate()
        super().tearDown()

    def add_model(self):
        self.login(self.super)
        r = self.post('/api/admin/catalog/models', {'label': 'Percentage of net collections (minimum)', 'billing': 'recurring',
                                                     'fields': [{'label': 'Percentage', 'kind': 'percent', 'required': True}, {'label': 'Minimum monthly fee', 'kind': 'amount'}],
                                                     'wording': 'Client shall pay {Percentage} of net collections each month. The minimum fee is {Minimum monthly fee} per month.'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn('6% of net collections', r.json()['preview'])
        return r.json()['id']

    def test_only_the_super_admin_changes_the_catalogue(self):
        self.login(self.u1)
        self.assertEqual(self.api.get('/api/admin/catalog').status_code, 403)
        self.assertEqual(self.post('/api/admin/catalog/services', {'name': 'X'}).status_code, 403)

    def test_service_gets_a_new_pricing_model_and_several_options_print_with_a_choice_line(self):
        model = self.add_model()
        view = self.api.get('/api/admin/catalog').json()
        ar = next(s for s in view['services'] if s['id'] == 'ar_denials')
        r = self.post('/api/admin/catalog/services/ar_denials', dict(ar, models=ar['models'] + [model]))
        self.assertEqual(r.status_code, 200, r.text)
        catalog_admin.invalidate()
        self.assertIn(model, [m['id'] for m in catalog_admin.catalog_map()['ar_denials']['models']])
        # the builder may now offer FTE and the new model together; the agreement lists both and asks the client to choose
        payload = dict(data(), entity=SCRIBE, document_types=['services'], services=[{'id': 'ar_denials', 'model': 'fte', 'rate': 1500, 'quantity': 2, 'hours': 40,
                       'options': [{'model': 'fte', 'rate': 1500, 'quantity': 2, 'hours': 40}, {'model': model, 'values': {'percentage': 6, 'minimum_monthly_fee': 2500}}]}])
        d = legacy.validate(payload)
        self.assertEqual([o['model'] for o in d['services'][0]['options']], ['fte', model])
        blocks = legacy.build_documents(d, 'SA-TEST')[0]['blocks']
        text = ' '.join(str(b.get('text', '')) + ' ' + str(b.get('rows', '')) for b in blocks)
        self.assertIn('Option A', text)
        self.assertIn('6% of net collections', text)
        self.assertIn('$2,500.00 per month', text)
        self.assertIn('Fee option selected by Client for AR and Denial Management', text)
        # a model the service may not use is refused
        with self.assertRaises(ValueError):
            legacy.validate(dict(payload, services=[{'id': 'digital_marketing', 'model': model, 'values': {'percentage': 6}}]))

    def test_a_retired_service_leaves_the_builder_and_a_new_service_prints_its_own_scope(self):
        self.login(self.super)
        view = self.api.get('/api/admin/catalog').json()
        tm = next(s for s in view['services'] if s['id'] == 'telemarketing')
        self.assertEqual(self.post('/api/admin/catalog/services/telemarketing', dict(tm, active=False)).status_code, 200)
        r = self.post('/api/admin/catalog/services', {'name': 'Eligibility Audits', 'category': 'on_demand', 'models': ['one_time'], 'description': 'A one-off audit.',
                                                       'scope': 'Reviews eligibility for the assigned accounts.\n- Checks coverage\n- Reports findings'})
        self.assertEqual(r.status_code, 200, r.text)
        catalog_admin.invalidate()
        ids = [s['id'] for s in self.api.get('/api/config').json()['catalog']]
        self.assertNotIn('telemarketing', ids)
        self.assertIn(r.json()['id'], ids)
        d = legacy.validate(dict(data(), entity=SCRIBE, document_types=['services'], services=[{'id': r.json()['id'], 'model': 'one_time', 'rate': 900, 'quantity': 1}]))
        text = ' '.join(b.get('text', '') for b in legacy.build_documents(d, 'SA-TEST')[0]['blocks'])
        self.assertIn('Eligibility Audits', text)
        self.assertIn('Checks coverage', text)


class AgreementTypes(Base):
    def test_a_type_from_a_word_file_appears_in_the_builder_and_generates_with_the_signature_block(self):
        self.login(self.super)
        raw = docx([('Title', 'Master Staffing Agreement'), ('', 'This agreement is made on {Effective date} between {Company name} and {Client name}.'),
                    ('Heading1', '1. Placement'), ('', 'The replacement guarantee is {Replacement guarantee (days)} days.'), ('ListParagraph', 'Dedicated staff')])
        r = self.post('/api/admin/doc-types', {'title': 'Master Staffing Agreement', 'prefix': 'MSA', 'description': 'Remote staff.', 'companies': [SCRIBE], 'active': True,
                                               'file': base64.b64encode(raw).decode(), 'file_name': 'msa.docx'})
        self.assertEqual(r.status_code, 200, r.text)
        key = r.json()['key']
        self.assertEqual([f['label'] for f in r.json()['fields']], ['Replacement guarantee (days)'])
        self.login(self.u1)
        types = {x['key']: x for x in self.api.get('/api/config').json()['doc_types']}
        self.assertIn(key, types)
        d = legacy.validate(dict(data(), entity=SCRIBE, document_types=['baa', key], custom_fields={key: {'replacement_guarantee_days': '30'}}))
        doc = legacy.build_documents(d, 'MSA-TEST')[1]
        self.assertEqual((doc['title'], doc['stem']), ('Master Staffing Agreement', 'MSA'))
        text = ' '.join(b['text'] for b in doc['blocks'])
        self.assertIn('between SCRIBE ALIGN LLC and', text)
        self.assertIn('30 days', text)
        self.assertIn('1. Placement', text)
        self.assertEqual(doc['blocks'][-1]['type'], 'signature')
        # another company may not use it
        with self.assertRaises(ValueError):
            legacy.validate(dict(data(), entity='EXTENDED HEALTH SERVICES INC.', document_types=[key]))

    def test_built_in_agreements_can_only_be_hidden(self):
        self.login(self.super)
        self.assertEqual(self.post('/api/admin/doc-types/builtin/nda', {'hidden': True}).status_code, 200)
        hidden = [x['key'] for x in self.api.get('/api/config').json()['doc_types'] if x.get('hidden')]
        self.assertEqual(hidden, ['nda'])
        self.assertEqual(self.post('/api/admin/doc-types/builtin/other', {'hidden': True}).status_code, 404)
        self.assertEqual(self.post('/api/admin/doc-types', {'title': 'Business Associate Agreement', 'file': base64.b64encode(docx([('', 'x')])).decode()}).status_code, 409)


class FollowUps(Base):
    def test_notes_on_an_envelope_are_seen_by_the_team_with_date_and_author(self):
        self.connect_mailbox()
        contract_id, client_id = self.contract(self.u1)
        self.login(self.u1)
        env = self.post('/api/esign/envelopes', {'contract_id': contract_id}).json()
        r = self.post('/api/followups', {'envelope_id': env['id'], 'channel': 'phone', 'body': 'Called the practice manager; signing on Friday.', 'next_on': '2026-11-20'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()['author'], r.json()['channel_label'], r.json()['next_on']), (self.u1['name'], 'Phone call', '2026-11-20'))
        # the generated agreement (not sent another way) can have notes too
        self.assertEqual(self.post('/api/followups', {'contract_id': contract_id, 'channel': 'email', 'body': 'Sent the PDF by email.'}).status_code, 200)
        self.login(self.admin)    # the team's Admin sees the notes of the team's clients
        notes = self.api.get(f'/api/followups?client_id={client_id}').json()['notes']
        self.assertEqual([n['channel'] for n in notes], ['email', 'phone'])
        self.assertEqual(len(self.api.get(f"/api/followups?envelope_id={env['id']}").json()['notes']), 2, 'the envelope shows the notes on its agreement too')
        self.login(self.u1)
        rows = self.api.get('/api/esign/envelopes?box=drafts').json()['envelopes']
        self.assertEqual(next(e for e in rows if e['id'] == env['id'])['followups']['count'], 1)
        self.login(self.ehs_user)  # another company: no access
        self.assertEqual(self.api.get(f'/api/followups?client_id={client_id}').status_code, 404)
        self.assertEqual(self.post('/api/followups', {'client_id': client_id, 'body': 'x'}).status_code, 404)

    def test_a_note_needs_text(self):
        contract_id, _ = self.contract(self.u1)
        self.login(self.u1)
        self.assertEqual(self.post('/api/followups', {'contract_id': contract_id, 'body': '  '}).status_code, 400)


class Margins(Base):
    def test_every_company_prints_with_the_same_side_margins(self):
        entities.invalidate()
        for e in entities.ENTITIES.values():
            self.assertEqual((e['left'], e['right']), (936, 936), e['name'])

    def test_the_migration_keeps_the_page_width(self):
        with db.engine.begin() as c:
            row = c.execute(select(db.companies.c.width, db.companies.c.left).where(db.companies.c.key == 'house_of_rcm')).one()
        self.assertEqual(row, (9026 + 2 * 1440 - 2 * 936, 936))


class Printing(Base):
    def test_print_all_has_every_document_and_the_certificate_and_is_framable_by_this_site_only(self):
        self.connect_mailbox()
        contract_id, _ = self.contract(self.u1)
        self.login(self.u1)
        env = self.post('/api/esign/envelopes', {'contract_id': contract_id}).json()
        self.post(f"/api/esign/envelopes/{env['id']}/draft", {'recipients': [dict(env['recipients'][0], name='Jane Example')],
                                                              'fields': [dict(f, recipient=f['recipient_id']) for f in env['fields']]})
        self.post(f"/api/esign/envelopes/{env['id']}/send")
        self.drain()
        client, base, state = self.signer(self.smtp.link(self.smtp.to('jane@client.test')[-1]))
        before = client.get(base + 'print-all')
        self.assertEqual(before.status_code, 200)
        from pypdf import PdfReader
        pages = len(PdfReader(io.BytesIO(before.content)).pages)
        self.assertEqual(self.complete_signing(client, base, client.get(base + 'api/state').json()).status_code, 200)
        self.drain()
        after = client.get(base + 'print-all')
        self.assertGreater(len(PdfReader(io.BytesIO(after.content)).pages), pages, 'the certificate is added once everyone has signed')
        self.assertEqual(after.headers['x-frame-options'], 'SAMEORIGIN')
        self.assertIn("frame-ancestors 'self'", after.headers['content-security-policy'])
        self.assertTrue(after.headers['content-disposition'].startswith('inline'))
        doc = client.get(base + f"document/{state['documents'][0]['id']}?inline=1")
        self.assertEqual(doc.headers['x-frame-options'], 'SAMEORIGIN')
        self.assertEqual(client.get(base + f"document/{state['documents'][0]['id']}").headers['x-frame-options'], 'DENY')

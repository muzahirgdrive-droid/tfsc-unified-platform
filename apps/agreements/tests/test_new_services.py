"""The standard Patient Help Desk Services and Demographics Updates (Credentialing add-on) services."""
import json
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_esign import SCRIBE, Base
from test_formatting_guard import docx_parts, paragraphs

import database as db
import format_guard as guard
import revised_content as content
import server

TWENTY_FOUR_SEVEN = '24-7 CONSULTANCY LLC'
WORDING = (Path(__file__).resolve().parent / 'fixtures' / 'phd_corrected_wording.md').read_text(encoding='utf-8')


def section(title):
    """Lines of one section of the owner's corrected wording (r5in/phd-wording.md), as printed for 24-7 Consultancy."""
    body = WORDING.split('## ' + title, 1)[1].split('\n## ', 1)[0]
    return [line.strip() for line in body.splitlines()[1:] if line.strip()]


def printed(lines):
    """The blocks the corrected agreement shows for those lines: lead-ins, labelled bullets and paragraphs."""
    out = []
    for line in lines:
        if line.startswith('• '):
            text = line[2:]
            out.append({'type': 'bullet', 'text': text, 'label': text.split(':', 1)[0]})
        else:
            out.append({'type': 'p', 'text': line})
    for i, block in enumerate(out[:-1]):
        if block['type'] == 'p' and block['text'].endswith(':') and out[i + 1]['type'] == 'bullet':
            block['type'] = 'lead'
    return out


def data(entity, services, **extra):
    d = dict(entity=entity, client_name='HILL COUNTRY ALLERGY & ASTHMA, PLLC', address='4 Lakeway Centre Court, Suit A Lakeway, TX 78734',
             effective_date='2026-11-04', document_types=['services'], governing_state='Texas', include_signature=False, services=services)
    d.update(extra)
    return server.validate(d)


def blocks_between(blocks, start, end):
    texts = [b.get('text') for b in blocks]
    first = texts.index(start) + 1
    return blocks[first:texts.index(end, first)]


PHD = dict(id='patient_help_desk', model='fte', rate=1650, quantity=1, hours=40)
CRED = dict(id='credentialing', model='rate_card', government_rate=150, commercial_rate=175)
DEMO = dict(id='demographics_updates', model='unit', rate=75, quantity=10)


class Catalogue(Base):
    def test_both_services_are_standard_with_their_pricing_models(self):
        catalog = {s['id']: s for s in content.CATALOG}
        phd, demo = catalog['patient_help_desk'], catalog['demographics_updates']
        self.assertEqual(phd['name'], 'Patient Help Desk Services')
        self.assertEqual(phd['category'], 'recurring')
        self.assertEqual([m['id'] for m in phd['models']], ['fte', 'pte', 'fixed', 'unit'])
        self.assertEqual(phd['unit'], 'completed call')
        self.assertEqual(demo['name'], 'Demographics Updates (Credentialing Add-on)')
        self.assertEqual([m['id'] for m in demo['models']], ['unit', 'one_time'])
        self.assertEqual(demo['unit'], 'payer demographic update')
        ids = [s['id'] for s in content.CATALOG]
        self.assertEqual(ids.index('demographics_updates'), ids.index('credentialing') + 1, 'listed right after Credentialing')

    def test_offered_to_every_standard_company_and_to_permitted_users(self):
        self.login(self.super)
        config = self.api.get('/api/config').json()
        self.assertTrue({'patient_help_desk', 'demographics_updates'} <= {s['id'] for s in config['catalog']})
        self.assertTrue(all(e['services'] is None for e in config['entities']), 'the built-in companies use the standard catalogue')
        email = f'phd-{uuid.uuid4().hex[:6]}@scribealign.test'
        r = self.post('/api/users', {'name': 'Help Desk Seller', 'email': email, 'role': 'user', 'companies': [SCRIBE], 'admin_id': self.admin['id'],
                                     'services': ['patient_help_desk', 'credentialing']})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['user']['services'], ['patient_help_desk', 'credentialing'], 'the new ids are valid permissions')
        with db.engine.begin() as c:
            c.execute(db.users.update().where(db.users.c.email == email).values(reset_required=False, password=self.hash))
        limited = {'email': email}
        self.login(limited)
        self.assertEqual({s['id'] for s in self.api.get('/api/config').json()['catalog']}, {'patient_help_desk', 'credentialing'})
        payload = dict(entity=SCRIBE, client_name='Permitted Practice LLC', address='1 Main St, Austin, TX 78701', effective_date='2026-11-04',
                       document_types=['services'], governing_state='Texas', services=[PHD, DEMO], output_root=str(db.OUTPUT))
        r = self.api.post('/api/generate', json=payload, headers={'X-CSRF-Token': self.csrf, 'Idempotency-Key': uuid.uuid4().hex})
        self.assertEqual(r.status_code, 403, r.text)
        self.assertIn('Demographics Updates', r.text)
        # "All services" (no restriction) includes both.
        self.login(self.super)
        uid = next(u['id'] for u in self.api.get('/api/users').json()['users'] if u['email'] == email)
        self.assertEqual(self.post(f'/api/users/{uid}', {'services': []}).status_code, 200)
        with db.engine.begin() as c:
            c.execute(db.users.update().where(db.users.c.id == uid).values(reset_required=False, password=self.hash))
        self.login(limited)
        self.assertTrue({'patient_help_desk', 'demographics_updates'} <= {s['id'] for s in self.api.get('/api/config').json()['catalog']})
        r = self.post('/api/preview', payload)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn('1.2 Demographics Updates (Credentialing Add-on)', json.dumps(r.json()['documents']))


class PatientHelpDeskWording(Base):
    def setUp(self):
        super().setUp()
        self.blocks = server.build_documents(data(TWENTY_FOUR_SEVEN, [PHD]), 'SA-TEST')[0]['blocks']

    def test_scope_matches_the_corrected_agreement(self):
        self.assertEqual(blocks_between(self.blocks, '1.1 Patient Help Desk Services', '2. Responsibilities'), printed(section('Scope')))

    def test_both_responsibility_blocks_match(self):
        part = blocks_between(self.blocks, '2.2 Patient Help Desk Services', '3. Fees')
        self.assertEqual(part[0], {'type': 'h3', 'text': '24-7 Consultancy Responsibilities'})
        self.assertEqual(part[1:2], printed(section('Company responsibilities')))
        self.assertEqual(part[2], {'type': 'h3', 'text': 'Client Responsibilities'})
        self.assertEqual(part[3:], printed(section('Client responsibilities')))

    def test_limitations_and_exclusions_match(self):
        part = blocks_between(self.blocks, '9.1 Patient Help Desk Services: Limitations and Exclusions', '10. Notices and General Terms')
        self.assertEqual(part, printed(section('Limitations and exclusions (section 9.x)')))

    def test_section_reference_follows_the_service_position(self):
        blocks = server.build_documents(data(TWENTY_FOUR_SEVEN, [CRED, PHD]), 'SA-TEST')[0]['blocks']
        text = json.dumps(blocks)
        self.assertIn('described in Section 1.2, as expressly defined', text)
        self.assertNotIn('Section 1.1, as expressly', text)

    def test_company_name_follows_the_contracting_company(self):
        scribe = json.dumps(server.build_documents(data(SCRIBE, [PHD]), 'SA-TEST')[0]['blocks'])
        self.assertIn('Scribe Align shall not independently receive, retain, or manage patient funds.', scribe)
        self.assertNotIn('24-7 Consultancy', scribe)
        self.assertIn('24-7 Consultancy shall not independently receive', json.dumps(self.blocks))

    def test_word_document_prints_bold_labels_and_a_left_aligned_lead_in(self):
        validated = data(TWENTY_FOUR_SEVEN, [PHD])
        xml = docx_parts(self.blocks, validated)['word/document.xml'].decode()
        for label in ('Incoming Call Management', 'System Access', 'Clinical Services', 'Confidentiality and Compliance'):
            paragraph = next(p for p in paragraphs(xml) if f'>{label}<' in p)
            self.assertIn('<w:numId w:val="9999"/>', paragraph)
            self.assertRegex(paragraph, f'<w:b/></w:rPr><w:t xml:space="preserve">{re.escape(label)}</w:t>')
        for lead in ('The Client shall:', 'The following limitations and exclusions shall apply:'):
            paragraph = next(p for p in paragraphs(xml) if f'>{lead}<' in p)
            self.assertIn('<w:jc w:val="left"/>', paragraph)


class Pricing(Base):
    def fee(self, service, entity=SCRIBE):
        validated = data(entity, [service])
        blocks = content.build_services(validated, 'SA-TEST')
        return validated['services'][0], blocks, json.dumps(blocks)

    def test_patient_help_desk_pricing_models(self):
        _, _, text = self.fee(PHD)
        self.assertIn('$1,650.00 per FTE per month x 1 position = $1,650.00 per month. Each position includes 40 scheduled hours per week.', text)
        _, _, text = self.fee(dict(PHD, model='pte', quantity=2, hours=20))
        self.assertIn('$1,650.00 per PTE per month x 2 positions = $3,300.00 per month. Each position includes 20 scheduled hours per week.', text)
        _, _, text = self.fee(dict(id='patient_help_desk', model='fixed', rate=2400))
        self.assertIn('$2,400.00 per month for the expressly described scope.', text)
        service, _, text = self.fee(dict(id='patient_help_desk', model='unit', rate=4.5, quantity=600))
        self.assertEqual(service['unit'], 'completed call', 'the unit defaults to a completed call')
        self.assertIn('$4.50 per completed call; monthly invoice equals that rate multiplied by documented completed units.', text)
        service, _, _ = self.fee(dict(id='patient_help_desk', model='unit', rate=4.5, unit='answered call'))
        self.assertEqual(service['unit'], 'answered call')

    def test_demographics_billing_per_update_or_as_a_project(self):
        service, _, text = self.fee(DEMO)
        self.assertEqual((service['unit'], service['category']), ('payer demographic update', 'recurring'))
        self.assertIn('$75.00 per payer demographic update; monthly invoice', text)
        self.assertIn('Recurring services are invoiced monthly in arrears.', text)
        self.assertNotIn('Each on-demand project has only the one or two scheduled invoices', text)
        service, _, text = self.fee(dict(id='demographics_updates', model='one_time', rate=750, quantity=1))
        self.assertEqual(service['category'], 'on_demand')
        self.assertIn('Total project fee: $750.00 ($750.00 x 1 projects or application units); one invoice upon delivery', text)
        self.assertIn('Each on-demand project has only the one or two scheduled invoices', text)
        with self.assertRaises(ValueError):
            data(SCRIBE, [dict(id='demographics_updates', model='fte', rate=10)])

    def test_staffing_fee_wording_is_singular_for_one_position(self):
        _, _, text = self.fee(dict(id='virtual_assistant', model='fte', rate=1000, quantity=1))
        self.assertIn('x 1 position = $1,000.00', text)
        self.assertNotIn('1 positions', text)
        _, _, text = self.fee(dict(id='virtual_assistant', model='fte', rate=1000, quantity=1.5))
        self.assertIn('x 1.5 positions', text)


class CredentialingAddOn(Base):
    def test_add_on_prints_as_its_own_service_after_credentialing(self):
        for entity in (SCRIBE, TWENTY_FOUR_SEVEN):
            with self.subTest(entity=entity):
                blocks = server.build_documents(data(entity, [CRED, DEMO, PHD]), 'SA-TEST')[0]['blocks']
                headings = [b['text'] for b in blocks if b['type'] == 'h2']
                self.assertEqual(headings[:3], ['1.1 Credentialing and Enrollment', '1.2 Demographics Updates (Credentialing Add-on)', '1.3 Patient Help Desk Services'])
                short = 'Scribe Align' if entity == SCRIBE else '24-7 Consultancy'
                scope = blocks_between(blocks, '1.2 Demographics Updates (Credentialing Add-on)', '1.3 Patient Help Desk Services')
                self.assertEqual(scope[0], {'type': 'lead', 'text': f'As an add-on to Credentialing and Enrollment, {short} shall update the provider and practice '
                                                                  'information each payer holds on file, including:'})
                self.assertEqual([b.get('label') for b in scope[1:]], ['Address Changes', 'Contact Details', 'Tax and Ownership Details', 'Group Roster Changes', 'CAQH ProView'])
                client = blocks_between(blocks, '2.3 Demographics Updates (Credentialing Add-on)', '2.4 Patient Help Desk Services')
                self.assertEqual(client[1], {'type': 'p', 'text': f'{short} shall prepare and submit each requested update to the applicable payer, track it to '
                                                                 'confirmation and keep Client informed of its status.'})
                self.assertEqual(client[3]['text'], 'The Client shall:')
                self.assertEqual([b['label'] for b in client[4:]], ['Accurate Information', 'Signatures and Attestations', 'Payer Correspondence'])
                self.assertIn(f'Inform {short} of any payer correspondence about a submitted update.', client[-1]['text'])
                text = json.dumps(blocks)
                self.assertIn(f"Payer processing times are outside {short}'s control. New payer enrollments are performed and billed under Credentialing and Enrollment.", text)
                self.assertIn('3.3 Demographics Updates (Credentialing Add-on)', text)

    def test_also_on_its_own(self):
        blocks = server.build_documents(data(TWENTY_FOUR_SEVEN, [DEMO]), 'SA-TEST')[0]['blocks']
        self.assertIn('1.1 Demographics Updates (Credentialing Add-on)', [b['text'] for b in blocks if b['type'] == 'h2'])

    def test_wording_has_no_unsupported_characters_and_every_list_item_is_labelled(self):
        for service in ('patient_help_desk', 'demographics_updates'):
            for text in content.original.SCOPES[service]:
                for block in guard.structure(text):
                    if block['type'] == 'bullet':
                        self.assertTrue(block.get('label'), block['text'])
                    self.assertNotIn('--', block['text'])

    def test_builder_offers_the_add_on_and_hints(self):
        app = (Path(__file__).resolve().parents[1] / 'app.js').read_text(encoding='utf-8')
        self.assertIn('const ADD_ON = {credentialing: "demographics_updates"}', app)
        for service in ('patient_help_desk', 'demographics_updates'):
            self.assertRegex(app, rf'\n    {service}: "')

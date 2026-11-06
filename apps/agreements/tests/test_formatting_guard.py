"""Formatting protection for typed or pasted service text (format_guard.py, format_guard.js, document_engine lists)."""
import copy
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
import contract_content as original  # noqa: E402
import document_engine as engine  # noqa: E402
import format_guard as guard  # noqa: E402
import revised_content as content  # noqa: E402
import server  # noqa: E402
from entities import get_entity  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / 'fixtures'
EXAMPLES = Path(__file__).resolve().parent / 'format_examples.json'
OWNER = json.loads((FIXTURES / 'owner_patient_help_desk.json').read_text(encoding='utf-8'))
NEW = {'patient_help_desk', 'demographics_updates'}


def corrected():
    """The owner's corrected Patient Help Desk wording (r5in/corrected.pdf), as the 24-7 Consultancy agreement prints it."""
    scope, company, client, exclusions = original.SCOPES['patient_help_desk']
    return [text.replace('Scribe Align', '24-7 Consultancy') for text in (scope, company, client, exclusions)]


def shape(blocks):
    return [(b['type'], b.get('label')) for b in blocks]


def services_data(entity='24-7 CONSULTANCY LLC', **extra):
    d = dict(entity=entity, client_name='HILL COUNTRY ALLERGY & ASTHMA, PLLC', address='4 Lakeway Centre Court, Suit A Lakeway, TX 78734',
             effective_date='2026-11-04', document_types=['services'], governing_state='Texas', include_signature=False)
    d.update(extra)
    return server.validate(d)


def docx_parts(blocks, data, entity=None):
    with tempfile.TemporaryDirectory() as tmp:
        path = engine.generate_docx(blocks, data, 'SA-TEST', Path(tmp) / 'out.docx', entity=entity)
        with ZipFile(path) as z:
            return {n: z.read(n) for n in z.namelist()}


def paragraphs(xml):
    return re.findall(r'<w:p>.*?</w:p>', xml, re.S)


class OwnerExample(unittest.TestCase):
    """The owner's Patient Help Desk text exactly as typed (rebuilt from r5in/generated.pdf) against the corrected agreement."""

    def test_scope_becomes_labelled_bullets(self):
        blocks = guard.structure(OWNER['scope'])
        self.assertEqual(shape(blocks), shape(guard.structure(corrected()[0])))
        self.assertEqual([b['text'] for b in blocks], [b['text'] for b in guard.structure(corrected()[0])], 'nothing is reworded')
        # The sixth line had no bullet symbol: it joins the list because it is a labelled line beside labelled bullets.
        self.assertTrue(OWNER['scope'].splitlines()[-1].startswith('Patient Communication & Follow-Up:'))
        self.assertEqual(blocks[-1]['label'], 'Patient Communication & Follow-Up')

    def test_client_responsibilities_have_a_lead_in_and_labelled_bullets(self):
        blocks = guard.structure(OWNER['responsibilities'])
        self.assertEqual(blocks[0], {'type': 'lead', 'text': 'The Client shall:'})
        self.assertEqual(shape(blocks), shape(guard.structure(corrected()[2])))
        self.assertEqual([b['label'] for b in blocks[1:]], ['System Access', 'Call Management', 'Billing and Payment Information', 'Insurance and COB Coordination',
                                                             'Operational Support', 'Policies and Compliance', 'Payment Infrastructure'])

    def test_exclusions_have_intro_lead_in_and_labelled_bullets(self):
        blocks = guard.structure(OWNER['exclusions'])
        self.assertEqual(shape(blocks), shape(guard.structure(corrected()[3])))
        self.assertEqual([b['type'] for b in blocks[:2]], ['p', 'lead'])
        self.assertEqual(blocks[1]['text'], 'The following limitations and exclusions shall apply:')

    def test_the_generated_agreement_matches_the_corrected_structure(self):
        data = services_data(custom_services=[OWNER])
        blocks = server.build_documents(data, 'SA-TEST')[0]['blocks']
        texts = [b.get('text') for b in blocks]
        scope = blocks[texts.index('1.1 Patient Help Desk Services') + 1:texts.index('2. Responsibilities')]
        self.assertEqual(shape(scope), shape(guard.structure(corrected()[0])))
        client = blocks[texts.index('Client Responsibilities') + 1:texts.index('3. Fees')]
        self.assertEqual(shape(client), shape(guard.structure(corrected()[2])))
        start = texts.index('9.1 Patient Help Desk Services: Limitations and Exclusions')
        exclusions = blocks[start + 1:texts.index('10. Notices and General Terms')]
        self.assertEqual(shape(exclusions), shape(guard.structure(corrected()[3])))
        # In Word: bold labels, a left-aligned lead-in, and no line breaks stretched across justified lines.
        xml = docx_parts(blocks, data)['word/document.xml'].decode()
        system = next(p for p in paragraphs(xml) if 'System Access' in p)
        self.assertIn('<w:numId w:val="9999"/>', system)
        self.assertRegex(system, r'<w:b/></w:rPr><w:t xml:space="preserve">System Access</w:t>')
        self.assertIn('<w:t xml:space="preserve">: Provide timely and authorized access', system)
        lead = next(p for p in paragraphs(xml) if '>The Client shall:<' in p)
        self.assertIn('<w:jc w:val="left"/>', lead)
        self.assertIn('<w:keepNext/>', lead)
        for p in paragraphs(xml):
            if 'Patient Help Desk' in p or 'System Access' in p or 'Clinical Services' in p:
                self.assertNotIn('<w:br/>', p)


class Rules(unittest.TestCase):
    def test_bullet_symbols_numbers_and_letters(self):
        blocks = guard.structure('• one\n● two\n▪ three\n➢ four\n► five\n✓ six\n- seven\n– eight\n— nine\n* ten\n· eleven\n twelve')
        self.assertEqual([b['type'] for b in blocks], ['bullet'] * 12)
        self.assertEqual(blocks[0]['text'], 'One', 'first letter capitalised')
        blocks = guard.structure('1. First\n2) Second\n(3) Third')
        self.assertEqual([(b['type'], b['style'], b.get('start')) for b in blocks], [('number', 'decimal', 1), ('number', 'decimal', None), ('number', 'decimal', None)])
        blocks = guard.structure('(a) approve scripts;\nb) name a contact; and\nc. keep the policy current.')
        self.assertEqual([(b['type'], b['style']) for b in blocks], [('number', 'letter')] * 3)
        self.assertEqual(blocks[0]['start'], 1)
        # not lists: decimals, years, times, a minus sign, emphasis
        self.assertEqual({b['type'] for b in guard.structure('1.5 hours per day\n2024. A year\n10:00 a.m. start\n-5% early payment\n*Note* applies')}, {'p'})

    def test_numbered_lists_restart_and_keep_their_first_number(self):
        blocks = guard.structure('Onboarding:\n1) Kick-off\n2) Access\n\nMonthly:\n1. Report\n2. Review\n\n3. Third\n4. Fourth')
        numbers = [b for b in blocks if b['type'] == 'number']
        self.assertEqual([b.get('start') for b in numbers], [1, None, 1, None, None, None])
        self.assertEqual(guard.structure('3. Third step\n4. Fourth step')[0]['start'], 3)

    def test_labels_are_short_capitalised_and_without_sentence_punctuation(self):
        self.assertEqual(guard.label_of('System Access: Provide timely access.'), 'System Access')
        self.assertEqual(guard.label_of('Coordination of Benefits (COB): Assist'), 'Coordination of Benefits (COB)')
        self.assertEqual(guard.label_of('Patient Communication & Follow-Up: Ensure'), 'Patient Communication & Follow-Up')
        self.assertEqual(guard.label_of('This sentence is a fairly long one that happens to contain a colon: not a label'), '')
        self.assertEqual(guard.label_of('E.g. something: else'), '')
        self.assertEqual(guard.label_of('lowercase start: text'), '')
        self.assertEqual(guard.label_of('Hours: 9:00 a.m.'), 'Hours')
        self.assertEqual(guard.label_of('Website: https://example.com'), 'Website')
        self.assertEqual(guard.label_of('The Client shall:'), '', 'a line ending with a colon is a lead-in, not a label')

    def test_label_runs_make_bullets_but_a_single_label_line_stays_a_paragraph(self):
        self.assertEqual(shape(guard.structure('Call hours: Monday to Friday\nLanguages: English and Spanish')), [('bullet', 'Call hours'), ('bullet', 'Languages')])
        self.assertEqual(shape(guard.structure('Note: all calls are recorded.')), [('p', None)])
        self.assertEqual(shape(guard.structure('Call hours: Monday to Friday\n\nLanguages: English and Spanish')), [('bullet', 'Call hours'), ('bullet', 'Languages')],
                         'blank lines between labelled lines do not break the run')

    def test_lead_ins(self):
        self.assertEqual(shape(guard.structure('The Client shall:\n- provide records')), [('lead', None), ('bullet', None)])
        self.assertEqual(shape(guard.structure('The following applies:\nAll work is remote.')), [('p', None), ('p', None)], 'only right before a list')
        self.assertEqual(shape(guard.structure('Deliverables:\n•\nMonthly report\n•\nQuarterly review')), [('lead', None), ('bullet', None), ('bullet', None)])

    def test_broken_lines_are_joined_and_short_lines_kept(self):
        joined = guard.structure('Client shall supply timely and accurate information, lawful access, necessary approvals and a responsive decision-\n'
                                 'maker, and shall maintain its required licenses and authority.')
        self.assertEqual(joined, [{'type': 'p', 'text': 'Client shall supply timely and accurate information, lawful access, necessary approvals and a '
                                   'responsive decision-maker, and shall maintain its required licenses and authority.'}])
        item = guard.structure('• Answering inbound patient calls about statements,\n  balances and insurance payments')
        self.assertEqual(item, [{'type': 'bullet', 'text': 'Answering inbound patient calls about statements, balances and insurance payments'}])
        wrapped = guard.structure('Provide timely and authorized access to the practice management system, electronic health records\n(EHR), patient billing records.')
        self.assertEqual(len(wrapped), 1)
        self.assertEqual(len(guard.structure('Up to 400 calls per month\nEnglish and Spanish\nTwo locations')), 3)
        self.assertEqual(guard.inspect('Patient Help Desk Services include\nanswering calls')[1]['joined'], 1)

    def test_spacing_is_cleaned_and_nothing_is_reworded(self):
        blocks, stats = guard.inspect('\tScope of  work:​\n•\tFirst item\n▪  Second   item\r\n')
        self.assertEqual(blocks, [{'type': 'lead', 'text': 'Scope of work:'}, {'type': 'bullet', 'text': 'First item'}, {'type': 'bullet', 'text': 'Second item'}])
        self.assertGreater(stats['spaces'], 0)
        self.assertEqual(guard.structure('eClinicalWorks access is required.')[0]['text'], 'eClinicalWorks access is required.')
        self.assertEqual(guard.structure(''), [])
        self.assertEqual(guard.structure('  \n\t\n'), [])

    def test_line_starting_dashes_survive_input_cleaning(self):
        cleaned = server.clean('Scope:\n— first item\n– second item\nA sentence — with a dash', 'Scope')
        self.assertEqual(shape(guard.structure(cleaned))[:3], [('lead', None), ('bullet', None), ('bullet', None)])


class Engine(unittest.TestCase):
    blocks = [{'type': 'title', 'text': 'SERVICES AGREEMENT'},
              {'type': 'lead', 'text': 'The Client shall:'},
              {'type': 'bullet', 'text': 'System Access: Provide access.', 'label': 'System Access'},
              {'type': 'number', 'text': 'Kick-off call', 'style': 'decimal', 'start': 1},
              {'type': 'number', 'text': 'Call hours: Monday to Friday', 'style': 'decimal', 'label': 'Call hours'},
              {'type': 'p', 'text': 'Between the lists.'},
              {'type': 'number', 'text': 'Third step', 'style': 'decimal', 'start': 3},
              {'type': 'number', 'text': 'Approve scripts;', 'style': 'letter', 'start': 1},
              {'type': 'number', 'text': 'Name a contact.', 'style': 'letter'},
              {'type': 'signature', 'text': 'Execution.'}]

    def data(self, entity='SCRIBE ALIGN LLC'):
        return dict(client_name='Example Practice LLC', effective_date='2026-11-06', entity=entity)

    def check_lists(self, parts):
        xml = parts['word/document.xml'].decode()
        numbering = parts['word/numbering.xml'].decode()
        ids = re.findall(r'<w:numId w:val="(\d+)"/>', xml)
        self.assertEqual(ids, ['9999', '10001', '10001', '10002', '10003', '10003'], 'each list has its own numbering instance')
        self.assertIn('<w:abstractNum w:abstractNumId="9998">', numbering)
        self.assertIn('<w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/>', numbering)
        self.assertIn('<w:numFmt w:val="lowerLetter"/><w:lvlText w:val="(%1)"/>', numbering)
        self.assertIn('<w:num w:numId="10002"><w:abstractNumId w:val="9998"/><w:lvlOverride w:ilvl="0"><w:startOverride w:val="3"/>', numbering)
        self.assertIn('<w:num w:numId="10003"><w:abstractNumId w:val="9997"/>', numbering)
        self.assertIn('<w:ind w:left="400" w:hanging="280"/>', numbering, 'a hanging indent like the bullets')
        call = next(p for p in paragraphs(xml) if 'Call hours' in p)
        self.assertRegex(call, r'<w:b/></w:rPr><w:t xml:space="preserve">Call hours</w:t>')
        lead = next(p for p in paragraphs(xml) if 'The Client shall:' in p)
        self.assertIn('<w:jc w:val="left"/>', lead)
        for name in ('word/document.xml', 'word/numbering.xml'):
            from xml.etree import ElementTree
            ElementTree.fromstring(parts[name])

    def test_lists_with_the_letterhead_numbering_part(self):
        for entity in ('SCRIBE ALIGN LLC', '24-7 CONSULTANCY LLC', 'EXTENDED HEALTH SERVICES INC.'):
            with self.subTest(entity=entity):
                parts = docx_parts(self.blocks, self.data(entity))
                self.check_lists(parts)
                with ZipFile(get_entity(entity)['template_path']) as z:
                    original_numbering = z.read('word/numbering.xml').decode()
                self.assertTrue(parts['word/numbering.xml'].decode().startswith(original_numbering.split('</w:numbering>')[0]), "the letterhead's own lists are kept")

    def test_lists_without_a_numbering_part(self):
        entity = dict(get_entity('SCRIBE ALIGN LLC'))
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'plain.docx'
            with ZipFile(entity['template_path']) as z, ZipFile(source, 'w') as out:
                for info in z.infolist():
                    if info.filename == 'word/numbering.xml':
                        continue
                    body = z.read(info.filename)
                    if info.filename == 'word/_rels/document.xml.rels':
                        body = re.sub(rb'<Relationship [^>]*Target="numbering.xml"/>', b'', body)
                    if info.filename == '[Content_Types].xml':
                        body = re.sub(rb'<Override PartName="/word/numbering.xml"[^>]*/>', b'', body)
                    out.writestr(info, body)
            entity.update(template_path=source, sha256=hashlib.sha256(source.read_bytes()).hexdigest())
            parts = docx_parts(self.blocks, self.data(), entity=entity)
        self.check_lists(parts)
        self.assertIn(b'Target="numbering.xml"', parts['word/_rels/document.xml.rels'])
        self.assertIn(b'/word/numbering.xml', parts['[Content_Types].xml'])

    def test_documents_without_numbered_lists_keep_todays_numbering(self):
        blocks = [{'type': 'bullet', 'text': 'Item'}, {'type': 'signature', 'text': 'Execution.'}]
        numbering = docx_parts(blocks, self.data())['word/numbering.xml'].decode()
        self.assertNotIn('9998', numbering)
        self.assertNotIn('startOverride', numbering)


class StandardServicesUnchanged(unittest.TestCase):
    """Standard services print exactly as before the formatting protection: their wording is one paragraph per section."""

    def test_every_standard_scope_text_is_a_single_unchanged_paragraph(self):
        for service, texts in original.SCOPES.items():
            if service in NEW:
                continue
            for text in texts:
                with self.subTest(service=service):
                    self.assertEqual(guard.structure(text), [{'type': 'p', 'text': text}])

    def test_agreements_with_standard_services_are_identical_to_the_legacy_composition(self):
        legacy = original._paragraphs     # the composition before format_guard: one paragraph per line
        catalog = [s for s in content.CATALOG if s['id'] not in NEW]
        for entity in ('SCRIBE ALIGN LLC', '24-7 CONSULTANCY LLC', 'EXTENDED HEALTH SERVICES INC.'):
            for spec in catalog:
                with self.subTest(entity=entity, service=spec['id']):
                    model = spec['models'][0]['id']
                    item = dict(id=spec['id'], model=model, rate=5, government_rate=150, commercial_rate=175, quantity=2, unit='completed case',
                                details='Two providers.\nFour payer applications per provider.', milestone1='Start', milestone2='Delivery')
                    data = services_data(entity, services=[item, dict(id='other', model='one_time', rate=10, details='A defined project.')] if spec['id'] != 'other' else [item])
                    now = content.build_services(copy.deepcopy(data), 'SA-TEST')
                    with patch.object(content, 'structure', legacy):
                        before = content.build_services(copy.deepcopy(data), 'SA-TEST')
                    self.assertEqual(now, before)

    def test_template_scopes_print_as_today(self):
        source = json.loads((APP / 'templates/service-scopes.json').read_text(encoding='utf-8'))
        data = services_data('SCRIBE ALIGN LLC', services=[dict(id='credentialing', model='one_time', rate=100)])
        blocks = content.build_services(data, 'SA-TEST')
        start = [b.get('text') for b in blocks].index('1.1 Credentialing and Enrollment') + 1
        expected = [dict(b, text=b['text'].replace('PRACTICE NAME', data['client_name'])) for b in source['credentialing']['scope']]
        self.assertEqual(blocks[start:start + len(expected)], expected)


class JavaScriptParity(unittest.TestCase):
    """format_guard.js (the builder's live preview) gives the same blocks and notes as format_guard.py."""

    @unittest.skipUnless(shutil.which('node'), 'node is not installed')
    def test_shared_examples(self):
        examples = json.loads(EXAMPLES.read_text(encoding='utf-8'))
        self.assertGreaterEqual(len(examples), 20)
        script = ("const g=require(process.argv[1]);const ex=JSON.parse(require('fs').readFileSync(process.argv[2],'utf8'));"
                  "process.stdout.write(JSON.stringify(ex.map(e=>g.inspect(e.text))))")
        result = subprocess.run(['node', '-e', script, str(APP / 'format_guard.js'), str(EXAMPLES)], capture_output=True, text=True, timeout=60, check=True)
        for example, js in zip(examples, json.loads(result.stdout), strict=True):
            blocks, stats = guard.inspect(example['text'])
            with self.subTest(example=example['name']):
                self.assertEqual(js['blocks'], blocks)
                self.assertEqual(js['stats'], stats)

    def test_builder_loads_the_guard_before_the_builder(self):
        html = (APP / 'index.html').read_text(encoding='utf-8')
        self.assertLess(html.index('/format_guard.js'), html.index('/app.js'))
        import portal
        self.assertIn('format_guard.js', portal.PRIVATE)


if __name__ == '__main__':
    unittest.main(verbosity=2)

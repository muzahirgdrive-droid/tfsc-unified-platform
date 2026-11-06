"""A long title (or company, or any typed value) is shown whole on the signing page and printed whole on the signed PDF."""
import base64
import io
import re
import sys
import unittest
from pathlib import Path

from pypdf import PdfReader
from reportlab.pdfbase import pdfmetrics

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from esign_support import agreement_pdf, signature_png
from test_esign import Base

import esign_pdf

APP = Path(__file__).resolve().parents[1]
TITLE = 'Senior Vice President, Revenue Cycle Management & Operations'      # 60 characters
PAGE_W = 612.0


def png_bytes(data_url: str) -> bytes:
    return base64.b64decode(data_url.split(',', 1)[1])


def drawn(page):
    """(text, x, baseline from the top, font size) of every text run on a page."""
    height = float(page.mediabox.height)
    out = []

    def visit(text, cm, tm, font, size):
        if text.strip():
            a, b, _, _, e, f = tm
            A, B, C, D, E, F = cm
            out.append((text.strip(), e * A + f * C + E, height - (e * B + f * D + F), float(size) * abs(a * A + b * C or 1)))
    page.extract_text(visitor_text=visit)
    return out


def stamp(fields):
    signer = {'name': 'Jane Example', 'signature_png': png_bytes(signature_png()), 'initials_png': png_bytes(signature_png('JE', (120, 60))),
              'signature_id': 'abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789'}
    return PdfReader(io.BytesIO(esign_pdf.stamp(agreement_pdf(pages=2), 'ENV-FIT1', fields, {'r1': signer})))


def run_of(page, text):
    return next(r for r in drawn(page) if r[0] == text)


class StampedTitle(unittest.TestCase):
    def test_sixty_character_title_is_whole_inside_the_margin_and_smaller(self):
        self.assertEqual(len(TITLE), 60)
        field = {'page': 2, 'kind': 'title', 'x': 339.0, 'y': 452.0, 'w': 150.0, 'h': 16.0, 'value': TITLE, 'recipient_id': 'r1'}
        page = stamp([field]).pages[1]
        _, x, base, size = run_of(page, TITLE)
        regular = esign_pdf.fonts()[0]
        right = x + pdfmetrics.stringWidth(TITLE, regular, size)
        self.assertAlmostEqual(x, field['x'] + 1, places=1, msg='drawn from the field’s left edge')
        self.assertLessEqual(right, PAGE_W - esign_pdf.FIT_RIGHT_MARGIN, 'inside the right margin')
        self.assertLess(size, 10.5, 'the font is reduced')
        self.assertGreaterEqual(size, 10.5 * esign_pdf.FIT_MIN_SCALE, 'by no more than the screen rule allows for this length')
        self.assertGreater(right, field['x'] + field['w'], 'it uses the room beyond the placed box instead of cutting the text')
        self.assertTrue(field['y'] < base < field['y'] + field['h'], 'on the line of the field')
        self.assertIn(TITLE, page.extract_text(), 'nothing is clipped')

    def test_a_value_that_fits_prints_exactly_as_before(self):
        field = {'page': 2, 'kind': 'title', 'x': 312.0, 'y': 360.0, 'w': 180.0, 'h': 18.0, 'value': 'Managing Member', 'recipient_id': 'r1'}
        _, x, _, size = run_of(stamp([field]).pages[1], 'Managing Member')
        self.assertEqual(size, 10.5)
        self.assertAlmostEqual(x, 313.0, places=1)

    def test_a_slightly_long_value_keeps_its_size_and_runs_to_the_right(self):
        value = 'Director of Revenue Cycle Operations'
        field = {'page': 2, 'kind': 'company', 'x': 339.0, 'y': 452.0, 'w': 120.0, 'h': 16.0, 'value': value, 'recipient_id': 'r1'}
        _, _, _, size = run_of(stamp([field]).pages[1], value)
        self.assertEqual(size, 10.5)

    def test_the_next_field_on_the_line_limits_the_room(self):
        title = {'page': 2, 'kind': 'title', 'x': 90.0, 'y': 452.0, 'w': 150.0, 'h': 16.0, 'value': TITLE, 'recipient_id': 'r1'}
        other = {'page': 2, 'kind': 'text', 'x': 339.0, 'y': 450.0, 'w': 150.0, 'h': 16.0, 'value': 'Client LLC', 'recipient_id': 'r2'}
        page = stamp([title, other]).pages[1]
        _, x, _, size = run_of(page, TITLE)
        right = x + pdfmetrics.stringWidth(TITLE, esign_pdf.fonts()[0], size)
        self.assertLessEqual(right, other['x'] - esign_pdf.FIT_GAP + 0.01)
        self.assertEqual(esign_pdf.fit_limit(title, [title, other], PAGE_W), other['x'] - esign_pdf.FIT_GAP)
        self.assertEqual(esign_pdf.fit_limit(other, [title, other], PAGE_W), PAGE_W - esign_pdf.FIT_RIGHT_MARGIN)

    def test_an_extreme_value_is_never_drawn_past_its_room(self):
        regular = esign_pdf.fonts()[0]
        for value in (TITLE * 2, 'W' * 400):
            text, size, fitted = esign_pdf.fit_value(value, regular, 10.5, 148, 300)
            self.assertTrue(fitted)
            self.assertLessEqual(pdfmetrics.stringWidth(text, regular, size), 300)
            self.assertGreaterEqual(size, esign_pdf.FIT_FLOOR)
        self.assertEqual(esign_pdf.fit_value(TITLE * 2, regular, 10.5, 148, 300)[0], TITLE * 2, 'shrunk, not shortened')
        self.assertTrue(esign_pdf.fit_value('W' * 400, regular, 10.5, 148, 300)[0].endswith('…'), 'only beyond the smallest size is it shortened')


class SamePageRule(unittest.TestCase):
    def test_signing_page_uses_the_same_constants(self):
        js = (APP / 'sign.js').read_text(encoding='utf-8')
        defaults = re.search(r'const FIT_DEFAULT = \{right_margin: ([\d.]+), min_scale: ([\d.]+), gap: ([\d.]+), floor: ([\d.]+)\}', js)
        self.assertIsNotNone(defaults)
        self.assertEqual(tuple(float(x) for x in defaults.groups()), (esign_pdf.FIT_RIGHT_MARGIN, esign_pdf.FIT_MIN_SCALE, esign_pdf.FIT_GAP, esign_pdf.FIT_FLOOR))
        self.assertIn('...(S.state?.fit || {})', js, 'the server’s constants take precedence')
        self.assertIn('if (o.x >= f.x + f.w - 0.5 && o.y < f.y + f.h && o.y + o.h > f.y) limit = Math.min(limit, o.x - rule.gap);', js)


class EndToEnd(Base):
    def test_signer_title_of_sixty_characters_on_the_signed_pdf(self):
        self.connect_mailbox()
        contract_id, _ = self.contract(self.u1, include_signature=False)
        self.login(self.u1)
        r = self.post('/api/esign/envelopes', {'contract_id': contract_id})
        self.assertEqual(r.status_code, 200, r.text)
        env = r.json()
        client, company = env['recipients']
        people = [dict(client, name='Jane Example', key='a'), dict(company, key='b')]
        fields = [dict(f, recipient=f['recipient_id']) for f in env['fields']]
        r = self.post(f"/api/esign/envelopes/{env['id']}/draft", {'version': env['version'], 'recipients': people, 'fields': fields})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.post(f"/api/esign/envelopes/{env['id']}/send").status_code, 200)
        self.drain()
        jane, base, state = self.signer(self.smtp.link(self.smtp.to('jane@client.test')[0]))
        self.assertEqual(state['fit'], esign_pdf.fit_constants(), 'the signing page receives the shared rule')
        self.assertEqual(self.sign_post(jane, base, 'consent', state).status_code, 200)
        titles = [f for f in jane.get(base + 'api/state').json()['fields'] if f['kind'] == 'title']
        self.assertEqual(len(titles), 2, 'one title on each agreement')
        self.assertEqual(self.complete_signing(jane, base, state, {f['id']: TITLE for f in titles}).status_code, 200)
        self.drain()
        naeem, base2, state2 = self.signer(self.smtp.link(self.smtp.to('naeem@scribealign.test')[0]))
        self.assertEqual(self.complete_signing(naeem, base2, state2).status_code, 200)
        self.drain()
        self.login(self.u1)
        final = self.api.get(f"/api/esign/envelopes/{env['id']}").json()
        self.assertEqual(final['status'], 'completed')
        regular = esign_pdf.fonts()[0]
        for doc in final['documents']:
            pdf = PdfReader(io.BytesIO(self.api.get(f"/api/esign/documents/{doc['id']}/file?final=1").content))
            page = pdf.pages[-1]
            _, x, _, size = run_of(page, TITLE)
            self.assertLess(size, 10.5)
            self.assertLessEqual(x + pdfmetrics.stringWidth(TITLE, regular, size), float(page.mediabox.width) - esign_pdf.FIT_RIGHT_MARGIN)
        # The completed copies are kept as issued: another worker pass does not stamp them again.
        before = [d['final_sha256'] for d in final['documents']]
        self.drain()
        self.assertEqual([d['final_sha256'] for d in self.api.get(f"/api/esign/envelopes/{env['id']}").json()['documents']], before)


if __name__ == '__main__':
    unittest.main(verbosity=2)

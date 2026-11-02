"""Signer redesign (v6): company colours and a signature reference in the signing state, logos signers can load,
the violet "Signed by:" stamp in the completed PDF, and a signing page that stays within the content security policy."""
import base64
import io
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pypdf import PdfReader

import esign
import esign_pdf
from esign_support import agreement_pdf, signature_png
import test_esign

APP = Path(__file__).resolve().parents[1]


def png_bytes(data_url: str) -> bytes:
    return base64.b64decode(data_url.split(',', 1)[1])


class SigningState(test_esign.Base):
    def sent_envelope(self):
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
        return env

    def test_state_carries_company_colours_and_a_stable_signature_reference(self):
        self.sent_envelope()
        token = self.smtp.link(self.smtp.to('jane@client.test')[0])
        _, _, state = self.signer(token)
        brand = state['brand']
        self.assertEqual((brand['color'], brand['accent'], brand['dark']), ('#004B79', '#00AEEF', '#000F18'))
        self.assertEqual(brand['logo'], '/assets/brand/scribe.png', 'built-in logos stay public assets')
        self.assertRegex(state['signature_ref'], r'^[0-9A-F]{16}$')
        _, _, again = self.signer(token)
        self.assertEqual(again['signature_ref'], state['signature_ref'], 'the reference does not change between visits')

    def test_company_without_a_header_colour_falls_back_to_its_brand_colour(self):
        self.sent_envelope()
        token = self.smtp.link(self.smtp.to('jane@client.test')[0])
        real = esign.get_entity

        def old_style(name=None):
            entity = dict(real(name))
            entity.pop('dark', None)
            return entity
        with patch.object(esign, 'get_entity', old_style):
            _, _, state = self.signer(token)
        self.assertEqual(state['brand']['dark'], state['brand']['color'])
        self.assertNotIn('test', state, 'nothing new is required from older envelopes')

    def test_full_signing_still_completes_with_the_new_state(self):
        self.sent_envelope()
        jane, base, state = self.signer(self.smtp.link(self.smtp.to('jane@client.test')[0]))
        r = self.complete_signing(jane, base, state)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['recipient']['status'], 'signed')
        self.assertIn('signature_ref', r.json())
        self.assertIn('dark', r.json()['brand'])


class BrandLogo(unittest.TestCase):
    def test_custom_company_logo_is_embedded_for_signers(self):
        with tempfile.TemporaryDirectory() as tmp:
            logo = Path(tmp) / 'acme.png'
            logo.write_bytes(png_bytes(signature_png('ACME', (120, 40))))
            url = esign._brand_logo({'custom': True, 'logo_path': logo, 'logo_url': '/api/companies/acme/logo.png'})
            self.assertTrue(url.startswith('data:image/png;base64,'), 'signers have no account, so the protected logo route would fail')
            self.assertEqual(base64.b64decode(url.split(',', 1)[1]), logo.read_bytes())

    def test_missing_or_oversized_logo_falls_back_to_none(self):
        self.assertEqual(esign._brand_logo({'custom': True, 'logo_path': None, 'logo_url': '/api/companies/x/logo.png'}), '')
        self.assertEqual(esign._brand_logo({'custom': True, 'logo_path': '/nonexistent/logo.png'}), '')
        with tempfile.TemporaryDirectory() as tmp:
            big = Path(tmp) / 'big.png'
            big.write_bytes(b'\0' * 700_000)
            self.assertEqual(esign._brand_logo({'custom': True, 'logo_path': big}), '')

    def test_built_in_logo_keeps_its_public_url(self):
        self.assertEqual(esign._brand_logo({'custom': False, 'logo_url': '/assets/brand/24-7.png'}), '/assets/brand/24-7.png')


class FinalStamp(unittest.TestCase):
    def stamped(self):
        raw = agreement_pdf(pages=2)
        signer = {'name': 'Jane Example', 'signature_png': png_bytes(signature_png()), 'initials_png': png_bytes(signature_png('JE', (120, 60))),
                  'signature_id': 'abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789'}
        fields = [{'page': 1, 'kind': 'initials', 'x': 520, 'y': 740, 'w': 50, 'h': 24, 'value': 'true', 'recipient_id': 'r1'},
                  {'page': 2, 'kind': 'signature', 'x': 312, 'y': 300, 'w': 180, 'h': 40, 'value': 'true', 'recipient_id': 'r1'},
                  {'page': 2, 'kind': 'title', 'x': 312, 'y': 360, 'w': 180, 'h': 18, 'value': 'Managing Member', 'recipient_id': 'r1'}]
        return PdfReader(io.BytesIO(esign_pdf.stamp(raw, 'ENV-TEST1', fields, {'r1': signer})))

    def test_signature_has_violet_bracket_signed_by_and_short_id(self):
        pdf = self.stamped()
        text = pdf.pages[1].extract_text()
        self.assertIn('Signed by:', text)
        self.assertIn('ABCDEF0123456789', text)
        self.assertIn('Managing Member', text)
        self.assertNotIn('ABCDEF0123456789ABCDEF', text, 'only the short form of the ID is printed on the page')
        content = pdf.pages[1].get_contents().get_data().decode('latin-1')
        self.assertRegex(content, r'0?\.29\d* 0?\.16\d* 0?\.81\d* RG', 'the bracket is drawn in the signing violet')

    def test_initials_are_plain_ink_without_a_bracket(self):
        pdf = self.stamped()
        content = pdf.pages[0].get_contents().get_data().decode('latin-1')
        self.assertNotRegex(content, r'0?\.29\d* 0?\.16\d* 0?\.81\d* RG', 'initials carry no bracket')
        self.assertIn('Do', content, 'the initials image is drawn')
        self.assertNotIn('Signed by:', pdf.pages[0].extract_text())


class SigningPage(unittest.TestCase):
    def test_page_has_no_inline_script_or_style(self):
        html = (APP / 'sign.html').read_text()
        self.assertNotRegex(html, r'<script(?![^>]*\bsrc=)[^>]*>', 'script-src is self only')
        self.assertNotIn('<style', html, 'style-src is self only')
        self.assertNotRegex(html, r'\sstyle="', 'no inline style attributes')
        self.assertNotRegex(html, r'\son[a-z]+="', 'no inline event handlers')
        for needle in ('sign.js', 'sign.css', 'id="guide"', 'id="test-strip"', 'id="rail"', 'id="progress-bar"', 'id="more-menu"', 'id="preview-id"'):
            self.assertIn(needle, html)

    def test_every_element_the_script_uses_exists(self):
        html = (APP / 'sign.html').read_text()
        js = (APP / 'sign.js').read_text()
        ids = set(re.findall(r'\$\("([a-z0-9-]+)"\)', js)) | set(re.findall(r'show\("([a-z0-9-]+)"', js))
        ids |= {i for group in re.findall(r'\[((?:"[a-z0-9-]+",\s*)+"[a-z0-9-]+")\]\.forEach\(id => show', js) for i in re.findall(r'"([a-z0-9-]+)"', group)}
        present = set(re.findall(r'id="([a-z0-9-]+)"', html))
        self.assertFalse(sorted(ids - present), 'every id the script looks up is in the page')

    def test_guided_order_sorts_by_document_then_initials_then_position(self):
        js = (APP / 'sign.js').read_text()
        self.assertIn('(a.kind === "initials" ? 0 : 1) - (b.kind === "initials" ? 0 : 1)', js)
        self.assertIn('a.page - b.page || a.y - b.y || a.x - b.x', js)

    @unittest.skipUnless(shutil.which('node'), 'node is not installed')
    def test_script_parses(self):
        out = subprocess.run(['node', '--check', str(APP / 'sign.js')], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)


if __name__ == '__main__':
    unittest.main()

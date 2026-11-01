"""Verified sender: the BIMI logo and mark certificate (Gmail logo and blue tick), hosted by the portal."""
import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import companies
from test_esign import Base

GOOD_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" version="1.2" baseProfile="tiny-ps" viewBox="0 0 100 100">'
            '<title>Scribe Align</title><rect width="100" height="100" fill="#004B79"/></svg>')


def mark_certificate(mark: str, days: int = 365) -> str:
    """A self-signed certificate shaped like a BIMI mark certificate (only for these tests)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'Scribe Align LLC'),
                      x509.NameAttribute(x509.ObjectIdentifier(companies.MARK_TYPE_OID), mark)])
    issuer = x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'Example Mark CA')])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(issuer).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=max(1, 10 - days))).not_valid_after(now + dt.timedelta(days=days))
            .add_extension(x509.ExtendedKeyUsage([x509.ObjectIdentifier(companies.BIMI_EKU)]), critical=False)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('scribealign.test')]), critical=False)
            .sign(key, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM).decode()


class LogoRules(unittest.TestCase):
    def test_valid_tiny_ps_logo(self):
        self.assertEqual(companies.bimi_svg_check(GOOD_SVG)['warnings'], [])

    def test_rejects_unsafe_or_wrong_profile(self):
        for bad in (GOOD_SVG.replace('baseProfile="tiny-ps"', 'baseProfile="tiny"'),
                    GOOD_SVG.replace('<title>Scribe Align</title>', ''),
                    GOOD_SVG.replace('<rect', '<script>alert(1)</script><rect'),
                    GOOD_SVG.replace('<rect', '<image href="https://example.com/x.png"/><rect'),
                    GOOD_SVG.replace('<rect', '<rect onclick="x()"'),
                    '<!DOCTYPE svg [<!ENTITY x "y">]>' + GOOD_SVG, 'not svg at all', ''):
            with self.assertRaises(ValueError):
                companies.bimi_svg_check(bad)

    def test_certificate_kinds(self):
        self.assertEqual(companies.bimi_cert_info(mark_certificate('Registered Mark'))['kind'], 'VMC')
        self.assertEqual(companies.bimi_cert_info(mark_certificate('Prior Use Mark'))['kind'], 'CMC')
        info = companies.bimi_cert_info(mark_certificate('Registered Mark', days=-2))
        self.assertTrue(info['expired'])
        with self.assertRaises(ValueError):
            companies.bimi_cert_info('-----BEGIN CERTIFICATE-----\nnot a certificate\n-----END CERTIFICATE-----')

    def test_readiness_lists_what_is_missing(self):
        checks = [{'key': 'spf', 'state': 'ok'}, {'key': 'dkim', 'state': 'ok'}, {'key': 'dmarc', 'state': 'warning'}, {'key': 'bimi', 'state': 'missing', 'found': ''}]
        status = {'https': True, 'has_logo': True, 'has_cert': True, 'cert': {'kind': 'CMC'}, 'logo_url': 'https://p/bimi/x.svg', 'cert_url': 'https://p/bimi/x.pem'}
        out = companies.verified_tick(checks, status)
        self.assertFalse(out['ready'])
        self.assertTrue(any('DMARC' in b for b in out['blockers']))
        self.assertTrue(any('Common Mark' in b for b in out['blockers']))
        checks = [{'key': k, 'state': 'ok'} for k in ('spf', 'dkim', 'dmarc')] + [{'key': 'bimi', 'state': 'ok', 'found': 'v=BIMI1; l=https://p/bimi/x.svg; a=https://p/bimi/x.pem'}]
        self.assertTrue(companies.verified_tick(checks, status | {'cert': {'kind': 'VMC'}})['ready'])


class VerifiedSenderApi(Base):
    def test_only_super_admin_and_public_hosting(self):
        for user in (self.admin, self.u1):
            self.login(user)
            self.assertEqual(self.api.get('/api/admin/bimi/scribe').status_code, 403)
            self.assertEqual(self.post('/api/admin/bimi/scribe', {'svg': GOOD_SVG}).status_code, 403)
        self.login(self.super)
        r = self.post('/api/admin/bimi/scribe', {'svg': GOOD_SVG, 'pem': mark_certificate('Registered Mark')})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body['has_logo'] and body['has_cert'])
        self.assertEqual(body['cert']['kind'], 'VMC')
        self.assertIn('v=BIMI1; l=', body['record']['value'])
        self.assertIn('/bimi/scribe.pem', body['record']['value'])
        # invalid uploads are refused with a reason
        self.assertEqual(self.post('/api/admin/bimi/scribe', {'svg': GOOD_SVG.replace('tiny-ps', 'tiny')}).status_code, 400)
        # mailbox providers fetch the files without signing in
        self.api.cookies.clear()
        logo = self.api.get('/bimi/scribe.svg')
        self.assertEqual(logo.status_code, 200)
        self.assertTrue(logo.headers['content-type'].startswith('image/svg+xml'))
        self.assertIn('nosniff', logo.headers.get('x-content-type-options', ''))
        self.assertIn('BEGIN CERTIFICATE', self.api.get('/bimi/scribe.pem').text)
        self.assertEqual(self.api.get('/bimi/ehs.svg').status_code, 404)
        self.assertEqual(self.api.get('/bimi/scribe.txt').status_code, 404)

    def test_config_exposes_company_dark_colour(self):
        self.login(self.super)
        entities = self.api.get('/api/config').json()['entities']
        self.assertTrue(all(e.get('dark') for e in entities))


if __name__ == '__main__':
    unittest.main()

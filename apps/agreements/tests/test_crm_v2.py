"""CRM v2 integration on the TFSC side: one-step provisioning (§19.2) and signed events (§19.3)."""
import os, sys, unittest, uuid
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_portal as baseline
import portal, database as db, crm_bridge_v2 as v2
from sqlalchemy import select
from tfsc_bridge import canonical_body, headers

SECRET = 'synthetic-secret-' + 'b' * 48
ENT = list(portal.ENTITIES)


class ProvisioningTests(unittest.TestCase):
    add = baseline.PortalTests.add
    login = baseline.PortalTests.login
    post = baseline.PortalTests.post

    def setUp(self):
        baseline.PortalTests.setUp(self)
        self.env = patch.dict(os.environ, {'TFSC_CRM_SHARED_SECRET': SECRET}); self.env.start()
        # Director (CRM) is linked to the TFSC admin; Super Admin (CRM) to the TFSC super_admin.
        with db.engine.begin() as c:
            for crm, tfsc in (('crm-director', self.admin['id']), ('crm-super', self.super['id'])):
                c.execute(v2.account_links.insert().values(crm_user_id=crm, tfsc_user_id=tfsc, status='active', granted_by_crm_user_id='setup', created=1, updated=1))

    def tearDown(self):
        self.env.stop(); baseline.PortalTests.tearDown(self)

    def signed(self, body, path='/api/integrations/v1/crm/accounts'):
        raw = canonical_body(body)
        return self.api.post(path, content=raw, headers=headers(SECRET, 'POST', path, raw))

    def req(self, **kw):
        body = {'idempotency_key': uuid.uuid4().hex, 'crm_user_id': 'crm-agent-1', 'granting_crm_user_id': 'crm-director', 'email': 'new.person@example.com',
                'display_name': 'New Person', 'role': 'user', 'companies': [ENT[0]], 'action': 'grant'}
        body.update(kw); return body

    def test_director_grants_user_with_one_company(self):
        body = self.req()
        r = self.signed(body); self.assertEqual(r.status_code, 200, r.text)
        out = r.json(); self.assertTrue(out['created']); self.assertEqual(out['companies'], [ENT[0]])
        with db.engine.connect() as c:
            u = c.execute(select(db.users).where(db.users.c.id == out['tfsc_user_id'])).mappings().one()
        self.assertEqual((u['role'], u['companies'], u['admin_id'], u['reset_required']), ('user', [ENT[0]], self.admin['id'], True))
        again = self.signed(body); self.assertEqual(again.json(), out)  # idempotent replay

    def test_intersection_rule_and_admin_role_limits(self):
        self.assertEqual(self.signed(self.req(companies=[ENT[1]])).status_code, 403)  # director's TFSC rights don't cover it
        self.assertEqual(self.signed(self.req(role='admin')).status_code, 403)
        self.assertEqual(self.signed(self.req(role='super_admin')).status_code, 400)  # never from CRM (schema)
        ok = self.signed(self.req(role='admin', companies=ENT[:2], granting_crm_user_id='crm-super', crm_user_id='crm-mgr'))
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(self.signed(self.req(granting_crm_user_id='unknown')).status_code, 403)

    def test_verified_sso_identity_is_linked_and_regranting_moves_it(self):
        import oidc_support
        iss = 'https://sso.example.com/realms/tfsc'
        r = self.signed(self.req(oidc_issuer=iss, oidc_subject='kc-sub-1')); self.assertEqual(r.status_code, 200, r.text)
        uid = r.json()['tfsc_user_id']
        acc = oidc_support.accounts
        with db.engine.connect() as c:
            self.assertEqual(c.execute(select(acc.c.user_id).where(acc.c.issuer == iss, acc.c.subject == 'kc-sub-1')).scalar_one(), uid)
        # the same grant re-sent once the person's identity is known (first SSO sign-in in the CRM) links it too
        r = self.signed(self.req(oidc_issuer=iss, oidc_subject='kc-sub-2')); self.assertEqual(r.json()['tfsc_user_id'], uid)
        with db.engine.connect() as c:
            self.assertEqual(c.execute(select(acc.c.subject).where(acc.c.user_id == uid).order_by(acc.c.subject)).scalars().all(), ['kc-sub-1', 'kc-sub-2'])

    def test_existing_email_is_never_matched_implicitly(self):
        r = self.signed(self.req(email=self.u1['email'])); self.assertEqual(r.status_code, 409)
        r = self.signed(self.req(email=self.u1['email'], link_tfsc_user_id=self.u1['id'])); self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['tfsc_user_id'], self.u1['id'])

    def test_revoke_kills_access(self):
        out = self.signed(self.req(crm_user_id='crm-agent-2', email='x2@example.com')).json()
        r = self.signed(self.req(crm_user_id='crm-agent-2', email='x2@example.com', action='revoke')); self.assertEqual(r.status_code, 200, r.text)
        with db.engine.connect() as c:
            self.assertFalse(c.execute(select(db.users.c.active).where(db.users.c.id == out['tfsc_user_id'])).scalar_one())

    def test_bad_signature_and_replay(self):
        body = self.req(); raw = canonical_body(body)
        h = headers(SECRET, 'POST', '/api/integrations/v1/crm/accounts', raw)
        self.assertEqual(self.api.post('/api/integrations/v1/crm/accounts', content=raw, headers=h).status_code, 200)
        self.assertEqual(self.api.post('/api/integrations/v1/crm/accounts', content=raw, headers=h).status_code, 409)
        h2 = headers('w' * 40, 'POST', '/api/integrations/v1/crm/accounts', raw)
        self.assertEqual(self.api.post('/api/integrations/v1/crm/accounts', content=raw, headers=h2).status_code, 401)


class EventTests(unittest.TestCase):
    add = baseline.PortalTests.add
    login = baseline.PortalTests.login
    post = baseline.PortalTests.post
    payload = baseline.PortalTests.payload
    save = baseline.PortalTests.save

    def setUp(self):
        baseline.PortalTests.setUp(self)
        self.env = patch.dict(os.environ, {'TFSC_CRM_SHARED_SECRET': SECRET, 'TFSC_REQUIRE_APPROVAL': '1'}); self.env.start()

    def tearDown(self):
        self.env.stop(); baseline.PortalTests.tearDown(self)

    def test_review_lifecycle_emits_signed_events_for_crm_clients(self):
        from crm_bridge import links
        self.login(self.u1); cid = self.save()
        with db.engine.begin() as c:
            c.execute(links.insert().values(id=db.uid(), crm_client_id='lead-123', owner_id=self.u1['id'], client_id=cid))
        r = self.post('/api/reviews/submit', {'client_id': cid}).json()
        self.login(self.admin)
        self.post('/api/reviews/' + r['id'] + '/decision', {'decision': 'approve', 'version': r['version'], 'reason': 'Reviewed.'})
        with db.engine.connect() as c:
            topics = [x['topic'] for x in c.execute(select(v2.outbox).order_by(v2.outbox.c.created)).mappings()]
            env = c.execute(select(v2.outbox.c.payload)).scalars().first()
        self.assertEqual(topics, ['agreement.submitted', 'agreement.approved'])
        self.assertEqual(env['data']['crm_client_id'], 'lead-123'); self.assertEqual(env['source'], 'tfsc')
        sent = []
        class Resp: status_code = 200; text = ''
        def fake_post(url, content, headers, timeout):
            sent.append((url, content, headers)); return Resp()
        with patch.dict(os.environ, {'TFSC_CRM_URL': 'https://crm.example.test'}), patch('httpx.post', side_effect=fake_post):
            self.assertEqual(v2.deliver_pending(), 2)
        from tfsc_bridge import verify
        url, content, h = sent[0]
        self.assertTrue(url.endswith('/api/integrations/v1/tfsc/events'))
        verify(SECRET, 'POST', '/api/integrations/v1/tfsc/events', content, h)

    def test_non_crm_clients_emit_nothing(self):
        self.login(self.u1); cid = self.save(); self.post('/api/reviews/submit', {'client_id': cid})
        with db.engine.connect() as c:
            self.assertEqual(c.execute(select(v2.outbox)).first(), None)

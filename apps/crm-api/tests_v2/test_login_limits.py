"""Login rate limits fit an office behind one NAT address: many successful sign-ins from one IP are fine; repeated
failures from that IP are throttled; each account keeps its own strict limit."""
from fastapi.testclient import TestClient

from .conftest import PW


def test_office_nat_many_people_one_address(ws):
    from tfsc_crm.security import ratelimit
    ratelimit.reset_local()
    try:
        app = ws['app']
        emails = ['a@crm.test', 'b@crm.test', 'c@crm.test', 'sup@crm.test', 'sup2@crm.test', 'manager@crm.test']
        for i in range(60):  # 60 successful sign-ins from the same address (old limit: 30)
            r = TestClient(app, base_url='http://testserver').post('/api/auth/login', json={'email': emails[i % len(emails)], 'password': PW})
            assert r.status_code == 200, (i, r.text)
    finally:
        ratelimit.reset_local()


def test_failures_from_one_address_are_throttled_and_accounts_keep_their_limit(ws):
    from tfsc_crm.security import ratelimit
    ratelimit.reset_local()
    try:
        c = TestClient(ws['app'], base_url='http://testserver')
        codes = [c.post('/api/auth/login', json={'email': 'a@crm.test', 'password': 'wrong-password-123'}).status_code for _ in range(11)]
        assert codes[:10] == [401] * 10 and codes[10] == 429  # per-account: 10 per 15 min
        for i in range(100):
            c.post('/api/auth/login', json={'email': f'nobody{i}@crm.test', 'password': 'wrong-password-123'})
        r = c.post('/api/auth/login', json={'email': 'b@crm.test', 'password': PW})
        assert r.status_code == 429 and 'network' in r.json()['error']  # 100+ failures from this address
    finally:
        ratelimit.reset_local()

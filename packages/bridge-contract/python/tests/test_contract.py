import json
from pathlib import Path

import pytest

from tfsc_bridge import BridgeError, canonical_body, headers, sign, validate, verify

VECS = json.loads((Path(__file__).resolve().parents[2] / 'signature-vectors.json').read_text(encoding='utf-8'))
SECRET = 'x' * 40


@pytest.mark.parametrize('v', VECS)
def test_signature_vectors(v):
    assert sign(v['secret'], v['method'], v['path'], v['body'].encode(), v['timestamp'], v['nonce']) == v['signature']


def test_roundtrip_and_replay_and_expiry():
    body = canonical_body({'b': 2, 'a': 1})
    assert body == b'{"a":1,"b":2}'
    h = headers(SECRET, 'POST', '/x', body, now=1000)
    seen: set[str] = set()

    def once(n):
        dup = n in seen
        seen.add(n)
        return dup
    verify(SECRET, 'POST', '/x', body, h, now=1100, seen_nonce=once)
    with pytest.raises(BridgeError) as e:
        verify(SECRET, 'POST', '/x', body, h, now=1100, seen_nonce=once)
    assert e.value.status == 409
    with pytest.raises(BridgeError) as e:
        verify(SECRET, 'POST', '/x', body, h, now=1000 + 301)
    assert e.value.status == 401
    with pytest.raises(BridgeError):
        verify(SECRET, 'POST', '/x', body + b' ', h, now=1000)  # tampered body
    with pytest.raises(BridgeError):
        verify(SECRET, 'POST', '/y', body, h, now=1000)  # other path
    with pytest.raises(BridgeError) as e:
        verify('short', 'POST', '/x', body, h, now=1000)
    assert e.value.status == 503


def test_schemas():
    validate('account_provision_request', {'idempotency_key': 'abcdefgh', 'crm_user_id': 'u', 'granting_crm_user_id': 'g', 'email': 'a@b.test',
                                           'display_name': 'A', 'role': 'user', 'companies': ['SCRIBE ALIGN LLC'], 'action': 'grant'})
    with pytest.raises(BridgeError):
        validate('account_provision_request', {'idempotency_key': 'abcdefgh', 'crm_user_id': 'u', 'granting_crm_user_id': 'g', 'email': 'a@b.test',
                                               'display_name': 'A', 'role': 'super_admin', 'companies': [], 'action': 'grant'})
    with pytest.raises(BridgeError):
        validate('event_envelope', {'event_id': 'abcdefgh', 'topic': 'evil.topic', 'schema_version': 1, 'occurred_at': 'x', 'source': 'tfsc', 'data': {}})

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


def test_signing_refuses_weak_secret_and_sets_event_headers():
    with pytest.raises(BridgeError) as e:
        headers('short', 'POST', '/x', b'{}')
    assert e.value.status == 503
    h = headers(SECRET, 'POST', '/x', b'{}', event_id='evt-1234567890', schema='agreement.issued@1', now=1000, nonce='n' * 20)
    assert (h['X-TFSC-Event-Id'], h['X-TFSC-Schema'], h['X-TFSC-Nonce']) == ('evt-1234567890', 'agreement.issued@1', 'n' * 20)
    verify(SECRET, 'POST', '/x', b'{}', {k.lower(): v for k, v in h.items()}, now=1000)  # header names are case-insensitive


@pytest.mark.parametrize('bad', [{'X-TFSC-Timestamp': 'yesterday'}, {'X-TFSC-Nonce': 'short'}, {'X-TFSC-Nonce': 'has spaces in it!!!!'},
                                 {'X-TFSC-Signature': '0' * 64}])
def test_malformed_authentication_is_refused(bad):
    h = {**headers(SECRET, 'POST', '/x', b'{}', now=1000), **bad}
    with pytest.raises(BridgeError) as e:
        verify(SECRET, 'POST', '/x', b'{}', h, now=1000)
    assert e.value.status == 401


def test_validator_rules():
    import tfsc_bridge as tb
    schema = {'type': 'object', 'required': ['n'], 'additionalProperties': False, 'properties': {
        'n': {'type': 'integer'}, 'k': {'const': 'v1'}, 's': {'type': 'string', 'minLength': 2, 'maxLength': 4, 'pattern': '^[a-z]+$'},
        'xs': {'type': 'array', 'maxItems': 2, 'items': {'type': 'string'}}}}
    tb._check(schema, {'n': 1, 'k': 'v1', 's': 'abc', 'xs': ['a']}, 't')
    for bad in [{'n': True}, {'n': '1'}, {}, {'n': 1, 'k': 'v2'}, {'n': 1, 's': 'a'}, {'n': 1, 's': 'abcde'}, {'n': 1, 's': 'AB1'},
                {'n': 1, 'xs': ['a', 'b', 'c']}, {'n': 1, 'xs': [1]}, {'n': 1, 'extra': 1}]:
        with pytest.raises(BridgeError) as e:
            tb._check(schema, bad, 't')
        assert e.value.status == 400, bad

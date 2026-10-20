"""Hypothesis property tests for the refinement engine."""
import string

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from tfsc_refine import CREDENTIALS, ENTITY_SUFFIXES, GENERATIONAL, refine_email, refine_name, refine_phone

NORMALIZED_WORDS = set(CREDENTIALS) | set(ENTITY_SUFFIXES) | set(GENERATIONAL) | {'dr', 'doctor', 'incorporated', 'corporation'}

WORD = st.text(alphabet=string.ascii_letters + "'-", min_size=1, max_size=12).filter(lambda w: w[0].isalpha())
NAME = st.lists(WORD, min_size=1, max_size=5).map(' '.join)


@settings(max_examples=400)
@given(NAME)
def test_name_is_idempotent(raw):
    once = refine_name(raw).value
    assert refine_name(once).value == once


@settings(max_examples=400)
@given(NAME)
def test_name_keeps_every_letter_of_body(raw):
    """Never respell: the letters typed (case-insensitive) survive, in order.
    Credentials, entity suffixes and titles are excluded: they are deliberately normalized."""
    assume(not any(t.lower().strip('.') in NORMALIZED_WORDS for t in raw.split()))
    out = refine_name(raw).value.lower()
    letters = [c for c in raw.lower() if c.isalpha()]
    it = iter(c for c in out if c.isalpha())
    assert all(any(c == o for o in it) for c in letters)


@settings(max_examples=300)
@given(st.text(alphabet='0123456789 ()-.+x', max_size=25))
def test_phone_never_crashes_and_display_is_canonical(raw):
    r = refine_phone(raw)
    if r.e164:
        assert r.e164.startswith('+1') and len(r.e164) == 12
        assert r.display.startswith('(')


@settings(max_examples=300)
@given(st.text(max_size=60))
def test_email_never_crashes_and_has_no_whitespace(raw):
    r = refine_email(raw)
    assert not any(ch.isspace() for ch in r.value)

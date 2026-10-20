"""The shared vectors.json contract: the TypeScript port runs the same file."""
import json
from dataclasses import asdict
from pathlib import Path

import pytest
from tfsc_refine import parse_date, parse_tri, refine_email, refine_name, refine_phone, refine_row

V = json.loads((Path(__file__).resolve().parents[2] / 'vectors.json').read_text(encoding='utf-8'))
TODAY = V['today']


def test_vector_count():
    assert sum(len(v) for v in V.values() if isinstance(v, list)) >= 160


@pytest.mark.parametrize('case', V['names'], ids=lambda c: repr(c['input'])[:40])
def test_names(case):
    assert asdict(refine_name(case['input'])) == case['expected']


@pytest.mark.parametrize('case', V['phones'], ids=lambda c: repr(c['input'])[:40])
def test_phones(case):
    assert asdict(refine_phone(case['input'])) == case['expected']


@pytest.mark.parametrize('case', V['emails'], ids=lambda c: repr(c['input'])[:40])
def test_emails(case):
    assert asdict(refine_email(case['input'])) == case['expected']


@pytest.mark.parametrize('case', V['dates'], ids=lambda c: repr(c['input'])[:40])
def test_dates(case):
    assert asdict(parse_date(case['input'], TODAY)) == case['expected']


@pytest.mark.parametrize('case', V['tris'], ids=lambda c: repr(c['input']))
def test_tris(case):
    if case.get('error'):
        with pytest.raises(ValueError):
            parse_tri(case['input'])
    else:
        assert parse_tri(case['input']) == case['expected']


@pytest.mark.parametrize('case', V['rows'], ids=lambda c: repr(c['input'].get('name'))[:40])
def test_rows(case):
    assert refine_row(case['input'], TODAY) == case['expected']

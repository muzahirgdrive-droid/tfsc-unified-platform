"""Owner-mandated vectors from master command §10.1 (hand-written; never regenerate)."""
import pytest
from tfsc_refine import refine_name

REQUIRED = [
    ("john doe md", "John Doe, M.D.", []),
    ("DR. JANE A SMITH, DO", "Jane A. Smith, D.O.", []),
    ("holistic behavioral health llc", "Holistic Behavioral Health, LLC", []),
    ("SUNRISE PEDIATRICS, PA", "Sunrise Pediatrics, P.A.", []),
    ("mary o'neil-mcdonald np", "Mary O'Neil-McDonald, NP", []),
    ("center for aba therapy of ny inc", "Center for ABA Therapy of NY, Inc.", []),
    ("robert king jr dds", "Robert King Jr., D.D.S.", []),
    ("jane q. public, cp", "Jane Q. Public, CP", ["unrecognized_credential"]),
    ("  ALPHA   ortho  &  spine  group  ", "Alpha Ortho & Spine Group", []),
    ("smith", "Smith", []),
]


@pytest.mark.parametrize(('raw', 'expected', 'codes'), REQUIRED)
def test_required(raw, expected, codes):
    r = refine_name(raw)
    assert r.value == expected
    assert [i.code for i in r.issues] == codes
    if raw == 'smith':
        assert r.kind == 'unknown'


def test_never_invents_credentials_or_suffixes():
    for raw in ['john doe', 'acme clinic', 'jane roe', 'Sunrise Pediatrics']:
        r = refine_name(raw)
        assert r.credentials == [] and r.entity_suffix is None
        for token in ['M.D.', 'LLC', 'Inc.', 'P.A.']:
            assert token not in r.value


def test_comma_setting_off():
    assert refine_name('holistic behavioral health llc', comma_suffix=False).value == 'Holistic Behavioral Health LLC'

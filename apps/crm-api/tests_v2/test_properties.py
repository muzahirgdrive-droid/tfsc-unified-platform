"""Property tests (§26): the scoring ladder, the event-time score ledger and lock timing hold for any input."""
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import text

from tfsc_crm.domain import scoring
from tfsc_crm.pacific import business_date

from .conftest import new_phone

tri = st.sampled_from([True, False, None])
LADDER = {0, 10, 20, 30, 50, 100}


@st.composite
def outcomes(draw):
    """Toggle states an agent can save: Connected = Yes only together with Called = Yes."""
    called = draw(tri)
    connected = draw(tri) if called is True else draw(st.sampled_from([False, None]))
    return {'called': called, 'connected': connected, 'text_sent': draw(tri), 'email_sent': draw(tri)}


@given(o=outcomes(), committed=st.booleans(), strong=st.booleans(), signed=st.booleans(),
       conflict=st.sampled_from([None, 'amber', 'red']), deleted=st.booleans())
def test_ladder_values_and_caps(o, committed, strong, signed, conflict, deleted):
    s = scoring.ScoreInput(committed=committed, strong_confirmed=strong, signed_up=signed, conflict=conflict, deleted=deleted, **o)
    pts = scoring.ladder(s)
    assert pts in LADDER
    if deleted or not committed or o['called'] is not True:
        assert pts == 0  # no attempt, no points (data hunting and Called = No score nothing)
    elif conflict:
        assert pts == 10  # a duplicate conflict is capped at the attempt until resolved
    elif signed:
        assert pts == 100


@given(o=outcomes(), more=st.sampled_from(['connected', 'text_sent', 'email_sent', 'strong_confirmed', 'signed_up']))
def test_ladder_never_drops_when_an_outcome_is_added(o, more):
    base = scoring.ScoreInput(committed=True, **o)
    if more == 'connected' and o['called'] is not True:
        return
    up = scoring.ScoreInput(**{**base.__dict__, more: True})
    assert scoring.ladder(up) >= scoring.ladder(base)


@settings(max_examples=15, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])
@given(steps=st.lists(outcomes(), min_size=1, max_size=5))
def test_ledger_sums_to_the_points_after_any_sequence_of_edits(ws, steps):
    """Each edit writes old -> new as a ledger delta, so the ledger always adds up to the row's current points."""
    b = ws['b']
    first = steps[0]
    r = b.post('/api/activities', json={'name': 'Property Clinic', 'phone': new_phone(), **first})
    assert r.status_code == 200, r.text
    act = r.json()['activity']
    for o in steps[1:]:
        r = b.patch(f"/api/activities/{act['id']}", json={'version': act['version'], **o})
        assert r.status_code == 200, r.text
        act = r.json()['activity']
    final = steps[-1]
    expected = scoring.ladder(scoring.ScoreInput(committed=True, **final))
    from tfsc_crm.db.session import system_tx
    with system_tx() as c:
        ledger, points = c.execute(text('SELECT coalesce(sum(l.delta_points), 0), a.score_points FROM activities a '
                                        'LEFT JOIN score_ledger l ON l.activity_id = a.id WHERE a.id = :a GROUP BY a.score_points'),
                                   {'a': act['id']}).one()
    assert ledger == points == expected == act['points']


@given(start=st.datetimes(min_value=datetime(2026, 1, 1), max_value=datetime(2027, 12, 31), timezones=st.just(UTC)),
       step=st.timedeltas(min_value=timedelta(0), max_value=timedelta(days=3)))
def test_business_date_is_the_pacific_calendar_date_and_never_goes_back(start, step):
    pt = ZoneInfo('America/Los_Angeles')
    assert business_date(start) == start.astimezone(pt).date()
    assert business_date(start + step) >= business_date(start)
    assert abs((business_date(start) - start.date()).days) <= 1


@given(day=st.dates(min_value=date(2026, 1, 1), max_value=date(2027, 12, 31)), minutes=st.integers(0, 24 * 60 - 1))
def test_every_minute_of_a_pacific_day_including_dst_days_has_that_business_date(day, minutes):
    pt = ZoneInfo('America/Los_Angeles')
    start = datetime(day.year, day.month, day.day, tzinfo=pt).astimezone(UTC)
    end = (datetime(day.year, day.month, day.day, tzinfo=pt) + timedelta(days=1)).astimezone(UTC)  # 23, 24 or 25 h later
    t = start + timedelta(minutes=minutes)
    if t < end:
        assert business_date(t) == day


@given(lw=st.fixed_dictionaries({'import_min': st.integers(1, 30), 'import_max': st.integers(1, 60), 'import_minutes': st.integers(0, 120),
                                  'manual_minutes': st.integers(1, 240)}), source=st.sampled_from(['grid', 'import', 'manual']))
def test_lock_window_respects_the_configured_bounds(lw, source):
    import uuid

    from tfsc_crm.domain import activities
    from tfsc_crm.domain import settings as cfg
    real = cfg.get
    try:
        cfg.get = lambda c, org, key: lw if key == 'lock_windows' else real(c, org, key)
        w = activities._window(None, uuid.uuid4(), source)
    finally:
        cfg.get = real
    if source == 'manual':
        assert w == lw['manual_minutes']
    elif lw['import_min'] <= lw['import_max']:
        assert lw['import_min'] <= w <= lw['import_max']

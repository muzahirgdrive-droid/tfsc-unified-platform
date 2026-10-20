"""Pacific business clock (ADR-0013). All business dates use America/Los_Angeles."""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo('America/Los_Angeles')
PAKISTAN = ZoneInfo('Asia/Karachi')
WORLD_CLOCK = [
    ('America/New_York', 'Eastern', 'New York'),
    ('America/Chicago', 'Central', 'Chicago'),
    ('America/Denver', 'Mountain', 'Denver'),
    ('America/Phoenix', 'Arizona', 'Phoenix'),
    ('America/Los_Angeles', 'Pacific', 'Los Angeles'),
    ('America/Anchorage', 'Alaska', 'Anchorage'),
    ('Pacific/Honolulu', 'Hawaii', 'Honolulu'),
    ('Asia/Karachi', 'Pakistan', 'Karachi'),
]


def utcnow() -> datetime:
    return datetime.now(UTC)


def business_date(at: datetime | None = None) -> date:
    """Pacific calendar date of an instant (server time if omitted)."""
    at = at or utcnow()
    if at.tzinfo is None:
        raise ValueError('business_date requires an aware datetime')
    return at.astimezone(PACIFIC).date()


def pacific_local(d: date, t: time) -> tuple[datetime, str | None]:
    """Resolve a Pacific wall-clock time. Nonexistent times (spring-forward gap) move
    forward by the gap; ambiguous times (fall-back overlap) take the first (PDT) instance.
    Returns (aware UTC datetime, note) where note explains any adjustment."""
    naive = datetime.combine(d, t)
    first = naive.replace(tzinfo=PACIFIC, fold=0)
    second = naive.replace(tzinfo=PACIFIC, fold=1)
    round_trip = first.astimezone(UTC).astimezone(PACIFIC).replace(tzinfo=None)
    if round_trip != naive:  # gap
        shifted = (naive + timedelta(hours=1)).replace(tzinfo=PACIFIC)
        return shifted.astimezone(UTC), f'{t.strftime("%H:%M")} does not exist on {d.isoformat()} (DST starts); moved to {shifted.strftime("%H:%M %Z")}.'
    if first.utcoffset() != second.utcoffset():
        return first.astimezone(UTC), f'{t.strftime("%H:%M")} occurs twice on {d.isoformat()} (DST ends); using the first ({first.strftime("%Z")}).'
    return first.astimezone(UTC), None


def day_bounds(d: date) -> tuple[datetime, datetime]:
    start = datetime.combine(d, time(0), tzinfo=PACIFIC).astimezone(UTC)
    end = datetime.combine(d + timedelta(days=1), time(0), tzinfo=PACIFIC).astimezone(UTC)
    return start, end


def world_clock(now: datetime | None = None) -> dict:
    now = now or utcnow()
    pk_off = now.astimezone(PAKISTAN).utcoffset() or timedelta(0)
    items = []
    for zone, region, city in WORLD_CLOCK:
        z = ZoneInfo(zone)
        local = now.astimezone(z)
        off = local.utcoffset() or timedelta(0)
        # Next DST transition within 60 days (hour granularity, then minute precision).
        nxt = None
        probe = now
        for _ in range(60 * 24):
            probe2 = probe + timedelta(hours=1)
            if probe2.astimezone(z).utcoffset() != probe.astimezone(z).utcoffset():
                nxt = probe2
                break
            probe = probe2
        items.append({
            'zone': zone, 'region': region, 'city': city, 'time': local.isoformat(),
            'abbreviation': local.tzname(), 'utc_offset_hours': off.total_seconds() / 3600,
            'diff_vs_pakistan_hours': (off - pk_off).total_seconds() / 3600,
            'is_business_clock': zone == 'America/Los_Angeles',
            'calling_hours': 8 <= local.hour < 17 and local.weekday() < 5,
            'dst_change_in_days': None if nxt is None else max(0, (nxt - now).days),
        })
    return {'server_time': now.isoformat(), 'business_date': business_date(now).isoformat(), 'items': items}

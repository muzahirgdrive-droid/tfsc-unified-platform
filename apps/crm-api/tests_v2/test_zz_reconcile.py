"""Runs last: after every other test mutated data, rollups must equal a recompute from raw rows (§20.4),
and every activity's score-ledger sum must equal its ladder value (§20.2)."""
from sqlalchemy import text


def test_no_rollup_drift_and_ledger_sums_equal_ladder(ws):
    from tfsc_crm.db.session import system_tx, tx
    from tfsc_crm.domain import rollups
    with system_tx() as c:
        org = c.execute(text("SELECT org_id FROM users WHERE email='super@crm.test'")).scalar_one()
        days = [r[0] for r in c.execute(text('SELECT DISTINCT business_date FROM activities WHERE org_id = :o'), {'o': org})]
        bad = c.execute(text("""SELECT a.id, a.score_points, coalesce(sum(l.delta_points), 0) AS ledger
                                FROM activities a LEFT JOIN score_ledger l ON l.activity_id = a.id
                                WHERE a.org_id = :o GROUP BY a.id, a.score_points HAVING a.score_points <> coalesce(sum(l.delta_points), 0)"""),
                        {'o': org}).all()
    assert not bad, bad[:5]
    with tx(org) as c:
        drift = [d for day in days for d in rollups.reconcile(c, org, day, fix=False)]
    assert drift == []

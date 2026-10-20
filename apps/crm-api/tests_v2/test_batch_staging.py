"""Batched saves stage rollup and ledger writes per row. A row that fails after writing makes the fast path roll back
and the batch is saved again one savepoint per row: the failed row must leave nothing behind, every other row is saved,
and the totals equal what reconciliation recomputes from raw rows."""
from fastapi import HTTPException
from sqlalchemy import text

from .conftest import new_phone


def test_failed_row_after_staging_leaves_no_rollup_or_ledger_trace(ws, monkeypatch):
    from tfsc_crm.db.session import system_tx
    from tfsc_crm.domain import activities, rollups
    from tfsc_crm.pacific import business_date
    real = activities.create

    def flaky(c, actor, row, **kw):
        res = real(c, actor, row, **kw)  # writes the activity and stages its rollup + ledger deltas
        if row['name'] == 'staging clinic 2':
            raise HTTPException(409, 'simulated conflict after staging')
        return res

    monkeypatch.setattr(activities, 'create', flaky)
    rows = [{'name': f'staging clinic {i}', 'phone': new_phone(), 'called': 'Yes', 'connected': 'Yes' if i % 2 else 'No'} for i in range(6)]
    r = ws['c'].post('/api/activities/batch', json={'request_key': 'staging-test-0001', 'rows': rows, 'source': 'grid'})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out['saved'] == 5 and out['rejected'] == 1 and out['rows'][2]['status'] == 'rejected'
    with system_tx() as c:
        org = c.execute(text("SELECT org_id FROM users WHERE email = 'c@crm.test'")).scalar_one()
        assert rollups.reconcile(c, org, business_date(), fix=False) == []
        bad = c.execute(text('SELECT count(*) FROM (SELECT a.id FROM activities a LEFT JOIN score_ledger l ON l.activity_id = a.id '
                             'WHERE a.import_batch_id = :b GROUP BY a.id, a.score_points '
                             'HAVING coalesce(sum(l.delta_points), 0) <> a.score_points) x'), {'b': out['batch_id']}).scalar_one()
        orphans = c.execute(text('SELECT count(*) FROM score_ledger l LEFT JOIN activities a ON a.id = l.activity_id WHERE a.id IS NULL')).scalar_one()
    assert bad == 0 and orphans == 0

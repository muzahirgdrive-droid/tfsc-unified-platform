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


def test_concurrent_batches_sharing_numbers_in_opposite_order_do_not_deadlock(ws, caplog):
    import logging
    import threading
    phones = [new_phone() for _ in range(150)]
    rows = [{'name': f'overlap clinic {i}', 'phone': p, 'called': 'Yes'} for i, p in enumerate(phones)]
    jobs = [(ws['a'], 'overlap-forward-01', rows), (ws['b'], 'overlap-reverse-01', rows[::-1])]
    start, out = threading.Barrier(2), {}

    def paste(client, key, body):
        start.wait()
        out[key] = client.post('/api/activities/batch', json={'request_key': key, 'rows': body, 'source': 'grid'})

    with caplog.at_level(logging.WARNING, logger='tfsc_crm.file_import'):
        threads = [threading.Thread(target=paste, args=job) for job in jobs]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
    assert [out[k].status_code for k in ('overlap-forward-01', 'overlap-reverse-01')] == [200, 200]
    assert all(r.json()['saved'] == 150 for r in out.values())
    assert 'saving row by row' not in caplog.text  # a deadlock would abort the fast path


def test_duplicate_rules_inside_one_batch_match_row_by_row_saves(ws):
    """The batch fast path reads registry rows it locked up front; earlier rows' facts must still count for later rows."""
    from tfsc_crm.db.session import system_tx
    from tfsc_crm.domain import rollups
    from tfsc_crm.pacific import business_date
    theirs, twice, follow = new_phone(), new_phone(), new_phone()
    r = ws['b'].post('/api/activities', json={'name': 'Their Clinic', 'phone': theirs, 'called': True, 'followup_needed': True,
                                             'next_followup': '2026-12-01'})
    assert r.status_code == 200, r.text
    rows = [{'name': 'their clinic', 'phone': theirs, 'called': 'Yes'},
            {'name': 'twice clinic', 'phone': twice, 'called': 'Yes', 'connected': 'Yes'},
            {'name': 'twice clinic', 'phone': twice, 'called': 'Yes'},
            {'name': 'follow clinic', 'phone': follow, 'called': 'Yes', 'followup_needed': 'Yes', 'next_followup': '2026-12-02'},
            {'name': 'follow clinic', 'phone': follow, 'called': 'Yes'}]
    out = ws['c'].post('/api/activities/batch', json={'request_key': 'in-batch-dups-01', 'rows': rows, 'source': 'grid'}).json()
    assert out['saved'] == 5
    notices = [r['duplicate_notice'] for r in out['rows']]
    assert notices[0].startswith('Already in follow-up') and notices[1:] == [None] * 4  # own repeats are not duplicates
    ids = [r['activity_id'] for r in out['rows']]
    with system_tx() as c:
        q = text('SELECT p.state, p.state_owner_id, p.first_seen_activity_id, p.first_attempt_activity_id, p.first_connect_activity_id '
                 'FROM phone_registry p JOIN activities a ON a.phone_key = p.phone_key AND a.org_id = p.org_id WHERE a.id = :a')
        tw = c.execute(q, {'a': ids[1]}).one()
        fo = c.execute(q, {'a': ids[3]}).one()
        th = c.execute(q, {'a': ids[0]}).one()
        flags = c.execute(text("SELECT activity_id FROM flags WHERE kind = 'duplicate_followup' AND activity_id = ANY(:ids)"),
                          {'ids': ids}).scalars().all()
        org = c.execute(text("SELECT org_id FROM users WHERE email = 'c@crm.test'")).scalar_one()
        assert rollups.reconcile(c, org, business_date(), fix=False) == []
    assert [str(x) for x in tw[2:]] == [ids[1]] * 3  # first seen / attempt / connect stay with the first row
    assert fo[0] == 'followup' and str(fo[1]) == str(ws['c'].id) and str(fo[2]) == ids[3]
    assert th[0] == 'followup' and str(th[1]) == str(ws['b'].id)  # someone else's follow-up is not taken over
    assert [str(f) for f in flags] == [ids[0]]

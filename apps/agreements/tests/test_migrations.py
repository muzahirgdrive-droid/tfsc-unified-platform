"""Agreements schema migrations (Alembic): fresh install, pre-Alembic install, downgrade, and model parity."""
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

import database as db
import migrate
import portal  # noqa: F401  (registers every table)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='tfsc-mig-')
        self.engine = create_engine('sqlite:///' + str(Path(self.tmp.name) / 'm.db'))

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def test_fresh_database_matches_the_models(self):
        with self.engine.begin() as c:
            self.assertEqual(migrate.upgrade_to_head(c, db.meta), 'fresh')
        with self.engine.connect() as c:
            diff = compare_metadata(MigrationContext.configure(c), db.meta)
            self.assertEqual(diff, [])
            head = ScriptDirectory.from_config(migrate.config()).get_current_head()
            self.assertEqual(c.execute(text('SELECT version_num FROM alembic_version')).scalar_one(), head)

    def test_pre_alembic_install_is_stamped_and_keeps_rows(self):
        with self.engine.begin() as c:
            # An install from before Alembic has exactly the baseline tables.
            migrate.baseline_metadata(db.meta).create_all(c)
            c.execute(db.settings.insert().values(key='keep_me', value={'ok': True}))
        with self.engine.begin() as c:
            self.assertEqual(migrate.upgrade_to_head(c, db.meta), 'stamped')
        with self.engine.begin() as c:
            self.assertEqual(migrate.upgrade_to_head(c, db.meta), 'current')
            self.assertEqual(c.execute(text("SELECT count(*) FROM settings WHERE key = 'keep_me'")).scalar_one(), 1)
            self.assertEqual(compare_metadata(MigrationContext.configure(c), db.meta), [])

    def test_upgrade_with_signed_envelopes_in_place(self):
        # Rows that reference esign_recipients would block a table rebuild on SQLite; the upgrade must keep all of them.
        with self.engine.begin() as c:
            command.upgrade(migrate.config(c), '0002_esign')
        t = time.time()
        rows = [
            ("users", dict(id='u1', email='a@b.test', name='A', password='x', role='user', admin_id=None, companies='[]', active=1, reset_required=0, created=t)),
            ("clients", dict(id='c1', owner_id='u1', name='Client', name_key='k', address='', draft='{}', updated=t)),
            ("esign_envelopes", dict(id='e1', ref='AAAA-0001', owner_id='u1', entity='SCRIBE ALIGN LLC', client_id='c1', contract_id=None, subject='S', message='',
                                     status='sent', template_name=None, settings='{}', version=1, created=t, updated=t, expiry_warned=0, completion_attempts=0)),
            ("esign_documents", dict(id='d1', envelope_id='e1', position=0, name='D.pdf', source='upload', pdf=b'%PDF', sha256='0' * 64, pages=1, page_sizes='[[612, 792, 0]]',
                                     created=t)),
            ("esign_recipients", dict(id='r1', envelope_id='e1', role='signer', role_name='Client', routing_order=1, name='N', email='n@b.test', note='', status='sent',
                                      auth_failures=0, delivery='sent', reminders_sent=0, created=t, auth_method='Email link')),
            ("esign_fields", dict(id='f1', envelope_id='e1', document_id='d1', recipient_id='r1', page=1, kind='signature', x=1, y=1, w=100, h=30, required=1, label='',
                                  options='{}')),
            ("esign_tokens", dict(id='t' * 64, recipient_id='r1', via='email', created=t)),
            ("esign_sessions", dict(id='s' * 64, csrf='c', recipient_id='r1', verified=1, via='email', expires=t + 60)),
        ]
        with self.engine.begin() as c:
            for table, values in rows:
                c.execute(text(f"INSERT INTO {table} ({', '.join(values)}) VALUES ({', '.join(':' + k for k in values)})"), values)
        old_engine = db.engine
        db.engine = self.engine
        try:
            db.init_db()
        finally:
            db.engine = old_engine
        with self.engine.begin() as c:
            self.assertEqual(c.execute(text('SELECT version_num FROM alembic_version')).scalar_one(), ScriptDirectory.from_config(migrate.config()).get_current_head())
            self.assertEqual(c.execute(text("SELECT auth_method, progress FROM esign_recipients WHERE id='r1'")).one(), ('Email link', None))
            self.assertEqual(c.execute(text('SELECT count(*) FROM esign_fields')).scalar_one(), 1)
            self.assertEqual(compare_metadata(MigrationContext.configure(c), db.meta), [])

    def test_downgrade_removes_every_table(self):
        with self.engine.begin() as c:
            migrate.upgrade_to_head(c, db.meta)
        with self.engine.begin() as c:
            command.downgrade(migrate.config(c), 'base')
        self.assertEqual(set(inspect(self.engine).get_table_names()) - {'alembic_version'}, set())


if __name__ == '__main__':
    unittest.main()

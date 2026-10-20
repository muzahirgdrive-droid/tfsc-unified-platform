"""Agreements schema migrations (Alembic): fresh install, pre-Alembic install, downgrade, and model parity."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
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
            self.assertEqual(c.execute(text('SELECT version_num FROM alembic_version')).scalar_one(), migrate.BASELINE)

    def test_pre_alembic_install_is_stamped_and_keeps_rows(self):
        with self.engine.begin() as c:
            db.meta.create_all(c)
            c.execute(db.settings.insert().values(key='keep_me', value={'ok': True}))
        with self.engine.begin() as c:
            self.assertEqual(migrate.upgrade_to_head(c, db.meta), 'stamped')
        with self.engine.begin() as c:
            self.assertEqual(migrate.upgrade_to_head(c, db.meta), 'current')
            self.assertEqual(c.execute(text("SELECT count(*) FROM settings WHERE key = 'keep_me'")).scalar_one(), 1)

    def test_downgrade_removes_every_table(self):
        with self.engine.begin() as c:
            migrate.upgrade_to_head(c, db.meta)
        with self.engine.begin() as c:
            command.downgrade(migrate.config(c), 'base')
        self.assertEqual(set(inspect(self.engine).get_table_names()) - {'alembic_version'}, set())


if __name__ == '__main__':
    unittest.main()

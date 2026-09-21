"""Migration and data-integrity tests. Only temporary databases are changed."""

from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

from migrate_databases import PROJECT_DIR, migrate_database


class DatabaseSchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def baseline(self, mode='demo', name=None):
        path = self.root / (name or f'{mode}.sqlite')
        sql = 'legacy-production-v1.sql' if mode == 'production' else 'start-and-demo.sql'
        with closing(sqlite3.connect(path)) as c:
            c.executescript((PROJECT_DIR / 'schema' / sql).read_text())
            if mode == 'demo':
                c.execute("INSERT INTO crm_accounts (account_id, name, lifetime_revenue, outstanding_ar) VALUES ('A', 'Original name', 50000, 2000)")
                c.execute("INSERT INTO crm_contacts (contact_id, account_id, name) VALUES ('C', 'A', 'Contact')")
            c.execute("INSERT INTO website_snapshots (snapshot_id, source_url, fetched_at, name, raw_html) VALUES ('R', 'https://example.test/location', '2026-09-20', 'Website name', '<h1>Website name</h1>')")
            c.commit()
        return path

    def migrate(self, path, mode='demo', **kwargs):
        return migrate_database(path, mode, self.root / 'backups', **kwargs)

    def connect(self, path):
        c = sqlite3.connect(path)
        c.execute('PRAGMA foreign_keys=ON')
        self.addCleanup(c.close)
        return c

    def populated(self):
        path = self.baseline()
        self.migrate(path)
        c = self.connect(path)
        c.execute("INSERT INTO runs (run_id, started_at, status) VALUES ('R', '2026-09-20', 'complete')")
        c.execute("INSERT INTO proposals (proposal_id, proposal_key, classification, account_id, explanation, created_at) VALUES ('P', 'A|name=New', 'name_correction', 'A', 'Names differ', '2026-09-20')")
        c.commit()
        return c

    def approval(self, c):
        c.execute("""INSERT INTO decisions
            (decision_id, proposal_id, choice, approved_changes, decided_at, submitted_at)
            VALUES ('D', 'P', 'approved', '[{"action":"update","account_id":"A","values":{"name":"New"}}]', '2026-09-20', '2026-09-20')""")
        c.commit()

    def test_preserves_existing_rows_and_backfills_scrape_metadata(self):
        path = self.baseline()
        c = self.connect(path)
        c.execute("""CREATE TABLE runs (
            run_id TEXT PRIMARY KEY NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
            status TEXT NOT NULL CHECK(status IN ('running','complete','incomplete','failed')),
            locations_found INTEGER NOT NULL DEFAULT 0, locations_saved INTEGER NOT NULL DEFAULT 0,
            errors TEXT NOT NULL DEFAULT '[]')""")
        c.execute("INSERT INTO runs VALUES ('R','start','finish','complete',1,1,'[]')")
        c.commit()
        before = {t: c.execute(f'SELECT * FROM {t}').fetchall() for t in ('crm_accounts', 'crm_contacts', 'website_snapshots', 'runs')}
        backup = self.migrate(path)
        for table in ('crm_accounts', 'crm_contacts', 'website_snapshots'):
            self.assertEqual(before[table], c.execute(f'SELECT * FROM {table}').fetchall())
        self.assertEqual(before['runs'], c.execute(
            'SELECT run_id,started_at,finished_at,status,locations_found,locations_saved,errors FROM runs'
        ).fetchall())
        self.assertEqual(c.execute('SELECT website_snapshot_id,scraping_status,normalization_status,matching_status FROM runs').fetchone(), ('R', 'complete', 'pending', 'pending'))
        with closing(sqlite3.connect(backup)) as b:
            self.assertEqual(b.execute('SELECT * FROM runs').fetchall(), before['runs'])
            self.assertEqual(b.execute('PRAGMA user_version').fetchone()[0], 1)

    def test_migration_is_repeatable_without_changing_file(self):
        path = self.baseline()
        self.migrate(path)
        before = path.read_bytes()
        self.assertIsNone(self.migrate(path))
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(len(list((self.root / 'backups').glob('*.sqlite'))), 1)

    def test_production_has_same_schema_and_keeps_website(self):
        demo, production = self.baseline(), self.baseline('production')
        self.migrate(demo)
        self.migrate(production, 'production')
        a, b = self.connect(demo), self.connect(production)
        query = "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
        self.assertEqual(a.execute(query).fetchall(), b.execute(query).fetchall())
        self.assertEqual(b.execute('SELECT COUNT(*) FROM crm_accounts').fetchone()[0], 0)
        self.assertEqual(b.execute('SELECT COUNT(*) FROM website_snapshots').fetchone()[0], 1)

    def test_nonempty_production_snapshots_are_never_dropped(self):
        path = self.baseline('production')
        c = self.connect(path)
        c.execute("INSERT INTO crm_snapshots(snapshot_id,fetched_at,account_id) VALUES ('S','date','A')")
        c.commit()
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'nonempty'):
            self.migrate(path, 'production')
        self.assertEqual(before, path.read_bytes())

    def test_start_and_aliases_are_protected(self):
        start = self.baseline(name='start.sqlite')
        for name, kind in [('direct', None), ('demo.sqlite', 'symlink'), ('hard.sqlite', 'hardlink')]:
            path = start if kind is None else self.root / name
            if kind == 'symlink':
                path.symlink_to(start)
            elif kind == 'hardlink':
                path.hardlink_to(start)
            with self.assertRaisesRegex(ValueError, 'protected'):
                self.migrate(path, protected_start=start)

    def test_setup_after_reset_works_and_does_not_change_start(self):
        start = self.baseline(name='start.sqlite')
        before = hashlib.sha256(start.read_bytes()).hexdigest()
        demo = self.root / 'demo.sqlite'
        with closing(sqlite3.connect(start.as_uri() + '?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(demo)) as destination:
                source.backup(destination)
        self.migrate(demo, protected_start=start)
        c = self.connect(demo)
        for table in ('runs','normalized_crm_accounts','normalized_website_locations','proposals','run_proposals','decisions','change_history'):
            self.assertEqual(c.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
        self.assertEqual(c.execute('SELECT COUNT(*) FROM crm_accounts').fetchone()[0], 1)
        self.assertEqual(before, hashlib.sha256(start.read_bytes()).hexdigest())

    def test_schema_error_rolls_back_all_changes(self):
        path = self.baseline()
        c = self.connect(path)
        c.execute('CREATE TABLE unexpected (id TEXT)')
        c.commit()
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'tables do not match'):
            self.migrate(path)
        self.assertEqual(before, path.read_bytes())

    def test_active_run_blocks_migration(self):
        path = self.baseline()
        c = self.connect(path)
        c.execute("CREATE TABLE runs(run_id TEXT, status TEXT)")
        c.execute("INSERT INTO runs VALUES ('R','running')")
        c.commit()
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'running work'):
            self.migrate(path)
        self.assertEqual(before, path.read_bytes())

    def test_unique_keys_and_foreign_keys(self):
        c = self.populated()
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO proposals(proposal_id,proposal_key,classification,explanation,created_at) VALUES ('P2','A|name=New','name_correction','test','date')")
        c.execute("INSERT INTO run_proposals VALUES ('R','P')")
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO run_proposals VALUES ('R','P')")
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO run_proposals VALUES ('missing','P')")
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO normalized_website_locations(run_id,snapshot_id,source_url,address_fields_present) VALUES ('R','missing','url',0)")
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO normalized_crm_accounts(run_id,account_id,address_fields_present) VALUES ('R','A',1)")

    def test_decisions_and_history_constraints(self):
        c = self.populated()
        c.execute("INSERT INTO decisions(decision_id,proposal_id,choice,decided_at) VALUES ('D','P','rejected','date')")
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO decisions(decision_id,proposal_id,choice,decided_at) VALUES ('D2','P','approved','date')")
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO change_history(change_id,decision_id,attempted_at,result,after_values) VALUES ('H','D','date','succeeded','[]')")
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("UPDATE decisions SET approved_changes='[{}]' WHERE decision_id='D'")

    def test_failed_attempts_then_single_success_and_immutable_history(self):
        c = self.populated()
        self.approval(c)
        for attempt in ('H1', 'H2'):
            c.execute("INSERT INTO change_history(change_id,decision_id,attempted_at,result,error) VALUES (?,'D','date','failed','test error')", (attempt,))
        c.execute("INSERT INTO change_history(change_id,decision_id,attempted_at,result,after_values) VALUES ('H3','D','date','succeeded','[]')")
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO change_history(change_id,decision_id,attempted_at,result,after_values) VALUES ('H4','D','date','succeeded','[]')")
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("UPDATE decisions SET choice='rejected', approved_changes='[]' WHERE decision_id='D'")
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("DELETE FROM change_history WHERE change_id='H1'")

    def test_failed_chow_transaction_leaves_no_new_account(self):
        c = self.populated()
        self.approval(c)
        with self.assertRaises(sqlite3.IntegrityError):
            with c:
                c.execute("INSERT INTO crm_accounts(account_id,name) VALUES ('NEW','Replacement')")
                c.execute("UPDATE crm_accounts SET chow_current_account='NEW' WHERE account_id='A'")
                # Simulate a failure while saving the success record.
                c.execute("INSERT INTO change_history(change_id,decision_id,attempted_at,result,after_values) VALUES ('H','missing','date','succeeded','[]')")
        self.assertIsNone(c.execute("SELECT 1 FROM crm_accounts WHERE account_id='NEW'").fetchone())
        self.assertIsNone(c.execute("SELECT chow_current_account FROM crm_accounts WHERE account_id='A'").fetchone()[0])

    def test_legacy_scraper_functions_still_record_progress(self):
        c = self.populated()
        c.execute("INSERT INTO runs(run_id,started_at,status) VALUES ('R2','date','running')")
        self.assertEqual(c.execute("SELECT scraping_status,website_snapshot_id FROM runs WHERE run_id='R2'").fetchone(), ('running','R2'))
        c.execute("UPDATE runs SET status='incomplete' WHERE run_id='R2'")
        self.assertEqual(c.execute("SELECT scraping_status FROM runs WHERE run_id='R2'").fetchone()[0], 'incomplete')


if __name__ == '__main__':
    unittest.main()

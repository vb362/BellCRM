"""Exercise destructive reset only against isolated temporary databases."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import reset_demo as reset
from migrate_databases import migrate_database, PROJECT_DIR, VERSION
import server


class ResetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.baseline = self.root / 'start.sqlite'
        self.demo = self.root / 'demo.sqlite'
        self.production = self.root / 'production.sqlite'
        with closing(sqlite3.connect(self.baseline)) as c:
            c.executescript((PROJECT_DIR / 'schema/start-and-demo.sql').read_text())
            c.execute("INSERT INTO crm_accounts(account_id,name) VALUES ('A','Original facility')")
            c.execute("INSERT INTO crm_contacts(contact_id,account_id,name) VALUES ('C','A','Original contact')")
            c.execute("INSERT INTO website_snapshots(snapshot_id,source_url,fetched_at,name) VALUES ('S','https://example.test/facility','date','Original facility')")
            c.commit()
        for path, mode in ((self.demo, 'demo'), (self.production, 'production')):
            shutil.copy2(self.baseline, path)
            migrate_database(path, mode, self.root / 'migration-backups', protected_start=self.baseline)
        with closing(sqlite3.connect(self.demo)) as c:
            c.execute("UPDATE crm_accounts SET name='Edited facility'")
            c.execute("UPDATE crm_contacts SET name='Edited contact'")
            c.execute("INSERT INTO runs(run_id,started_at,status,run_type) VALUES ('R','date','complete','pipeline')")
            c.execute("INSERT INTO proposals(proposal_id,proposal_key,classification,account_id,explanation,created_at) VALUES ('P','key','name_correction','A','reason','date')")
            c.execute("INSERT INTO run_proposals VALUES ('R','P')")
            c.execute("INSERT INTO decisions(decision_id,proposal_id,choice,approved_changes,decided_at,submitted_at) VALUES ('D','P','approved','[{\"action\":\"update\"}]','date','date')")
            c.execute("INSERT INTO change_history(change_id,decision_id,attempted_at,result,after_values) VALUES ('H','D','date','succeeded','[]')")
            c.commit()
        self.baseline_hash = self.digest(self.baseline)
        self.production_hash = self.digest(self.production)

    def digest(self, path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def dump(self, path):
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as c:
            return '\n'.join(c.iterdump())

    def restore(self):
        return reset.reset_demo(self.demo, self.baseline, self.production)

    def assert_protected(self):
        self.assertEqual(self.baseline_hash, self.digest(self.baseline))
        self.assertEqual(self.production_hash, self.digest(self.production))

    def test_restore_backs_up_edits_and_history_and_preserves_production(self):
        before = self.dump(self.demo)
        backup = self.restore()
        self.assertEqual(before, self.dump(backup))
        self.assertEqual(backup.parent, self.root / 'backups')
        with closing(sqlite3.connect(self.demo)) as c:
            self.assertEqual(c.execute('SELECT name FROM crm_accounts').fetchone()[0], 'Original facility')
            self.assertEqual(c.execute('SELECT name FROM crm_contacts').fetchone()[0], 'Original contact')
            self.assertEqual(c.execute('SELECT COUNT(*) FROM website_snapshots').fetchone()[0], 1)
            for table in reset.table_names(c) - set(reset.BASE_TABLES):
                self.assertEqual(c.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0], 0, table)
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], VERSION)
            self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assert_protected()

    def test_second_reset_is_safe_and_saves_a_separate_backup(self):
        first = self.restore()
        restored = self.dump(self.demo)
        second = self.restore()
        self.assertNotEqual(first, second)
        self.assertEqual(restored, self.dump(second))
        self.assertEqual(restored, self.dump(self.demo))
        self.assert_protected()

    def test_version_three_demo_restores_without_migrating_original_before_backup(self):
        with closing(sqlite3.connect(self.demo)) as c:
            c.execute('DROP TABLE production_requests')
            c.execute('DROP TABLE production_plans')
            c.execute('PRAGMA user_version=3')
        before = self.dump(self.demo)
        backup = self.restore()
        self.assertEqual(before, self.dump(backup))
        with closing(sqlite3.connect(self.demo)) as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], VERSION)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM decisions').fetchone()[0], 0)
        self.assert_protected()

    def test_active_pipeline_or_scraper_blocks_reset(self):
        for kind in ('pipeline', 'scraper'):
            with self.subTest(kind=kind):
                with closing(sqlite3.connect(self.demo)) as c:
                    c.execute("UPDATE runs SET status='running',run_type=?", (kind,))
                    c.commit()
                before = self.dump(self.demo)
                with self.assertRaisesRegex(ValueError, 'run is active'):
                    self.restore()
                self.assertEqual(before, self.dump(self.demo))
        self.assertFalse((self.root / 'backups').exists())

    def test_production_submission_history_blocks_reset(self):
        with closing(sqlite3.connect(self.demo)) as c:
            c.execute("INSERT INTO production_plans(plan_id,created_at,plan_json,digest,status) VALUES ('X','date','{}','digest','complete')")
            c.commit()
        before = self.dump(self.demo)
        with self.assertRaisesRegex(ValueError, 'production submissions'):
            self.restore()
        self.assertEqual(before, self.dump(self.demo))

    def test_protected_paths_and_hardlinks_are_rejected(self):
        for protected in (self.baseline, self.production):
            with self.assertRaises(ValueError):
                reset.reset_demo(protected, self.baseline, self.production)
            folder = self.root / protected.stem
            folder.mkdir()
            alias = folder / 'demo.sqlite'
            alias.hardlink_to(protected)
            with self.assertRaisesRegex(ValueError, 'protected'):
                reset.reset_demo(alias, self.baseline, self.production)
        self.assert_protected()

    def test_missing_or_corrupt_baseline_does_not_change_demo(self):
        before = self.dump(self.demo)
        with self.assertRaises(ValueError):
            reset.reset_demo(self.demo, self.root / 'missing.sqlite', self.production)
        broken = self.root / 'broken.sqlite'
        broken.write_bytes(b'not a database')
        with self.assertRaises(sqlite3.DatabaseError):
            reset.reset_demo(self.demo, broken, self.production)
        self.assertEqual(before, self.dump(self.demo))

    def test_backup_failure_prevents_reset(self):
        before = self.dump(self.demo)
        with patch.object(reset, 'save_reset_backup', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.restore()
        self.assertEqual(before, self.dump(self.demo))

    def test_restore_failure_rolls_back_all_tables_and_audit_triggers(self):
        before = self.dump(self.demo)
        original = reset.validate
        calls = []
        def fail_after_restore(*args):
            calls.append(True)
            if len(calls) == 2:
                raise ValueError('simulated restore failure')
            return original(*args)
        with patch.object(reset, 'validate', side_effect=fail_after_restore):
            with self.assertRaisesRegex(ValueError, 'simulated restore failure'):
                self.restore()
        self.assertEqual(before, self.dump(self.demo))
        self.assert_protected()

    def test_other_writers_are_locked_during_backup_and_restore(self):
        original = reset.save_reset_backup
        def check_lock(path, folder):
            with closing(sqlite3.connect(path, timeout=0)) as writer:
                with self.assertRaisesRegex(sqlite3.OperationalError, 'locked'):
                    writer.execute("UPDATE crm_accounts SET name='Concurrent edit'")
            return original(path, folder)
        with patch.object(reset, 'save_reset_backup', side_effect=check_lock):
            self.restore()

    def test_live_worker_blocks_reset_even_after_run_finishes(self):
        app = server.Application(self.demo)
        with patch.object(app, 'worker') as worker:
            worker.is_alive.return_value = True
            with self.assertRaisesRegex(ValueError, 'run is active'):
                app.reset(self.baseline, self.production)

    def test_http_requires_test_session_local_origin_and_confirmation(self):
        http = server.make_server(0, self.demo, self.production, baseline_database=self.baseline)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        http.sessions['test-session'] = http.app
        http.sessions['prod-session'] = server.Application(self.production, mode='production')
        def call(body, token=None, origin=None):
            headers = {'Content-Type': 'application/json'}
            if token: headers['X-Bellhaven-Session'] = token
            if origin: headers['Origin'] = origin
            request = Request(f'http://127.0.0.1:{http.server_port}/api/test/reset',
                              data=json.dumps(body).encode(), headers=headers)
            with urlopen(request) as response:
                return json.load(response)
        before = self.dump(self.demo)
        for body, token, origin, status in [
            ({'confirm': True}, None, None, 401),
            ({}, 'test-session', None, 409),
            ({'confirm': 'true'}, 'test-session', None, 409),
            ({'confirm': True}, 'prod-session', None, 409),
            ({'confirm': True}, 'test-session', 'https://other.example', 403),
        ]:
            with self.assertRaises(HTTPError) as error:
                call(body, token, origin)
            self.assertEqual(error.exception.code, status)
        self.assertEqual(before, self.dump(self.demo))
        result = call({'confirm': True}, 'test-session')
        self.assertEqual(result['mode'], 'test')
        self.assertEqual(result['runs'], [])
        self.assertEqual(result['proposals'], [])
        self.assertEqual(result['accounts'][0]['name'], 'Original facility')
        self.assertTrue((self.root / 'backups' / result['reset']['backup_file']).is_file())
        self.assert_protected()


if __name__ == '__main__':
    unittest.main()

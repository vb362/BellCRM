"""Check database handoff and failure behavior using only temporary databases."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import normalize_data as normalizer


class NormalizeDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'data').mkdir()
        self.path = self.root / 'data' / 'demo.sqlite'
        self.c = sqlite3.connect(self.path)
        self.addCleanup(self.c.close)
        self.c.executescript((normalizer.PROJECT_DIR / 'schema' / 'working.sql').read_text())
        self.c.execute('PRAGMA user_version=2')
        self.c.execute('PRAGMA foreign_keys=ON')
        self.c.execute("""INSERT INTO crm_accounts VALUES (
            'A', 'Bellhaven Health Care Centre', 'P', 'Parent',
            '123 West Main Street', ' Dayton ', 'OH', '04501', 'Nursing', 'Active',
            '555-0100', 50000.25, 2000.75, 'replacement', 'survivor', 'Keep this note', 0, 'original-time')""")
        self.c.execute("""INSERT INTO website_snapshots
            (snapshot_id,source_url,fetched_at,name,street,city,state,zip,care_offerings,raw_html)
            VALUES ('SNAPSHOT','https://example.test/facility','date','Bellhaven Healthcare Center',
            '123 W Main St','Dayton','OH','04501','["Nursing"]','<h1>Saved page</h1>')""")
        # A pipeline may still be running overall even though scraping is complete.
        self.c.execute("""INSERT INTO runs
            (run_id,started_at,status,run_type,website_snapshot_id,scraping_status,locations_found,locations_saved,errors)
            VALUES ('RUN','date','running','pipeline','SNAPSHOT','complete',1,1,
            '[{"stage":"scraping","severity":"warning","message":"Keep this warning"}]')""")
        self.c.commit()

    def run_normalizer(self, run_id='RUN'):
        return normalizer.normalize_database(self.path, run_id)

    def test_name_separators_and_leading_article_preserve_meaningful_words(self):
        for name in ('The Arbors at Bellhaven - Dayton', 'Arbors at Bellhaven Dayton',
                     ' THE Arbors at Bellhaven – Dayton ', 'The Arbors at Bellhaven — Dayton'):
            self.assertEqual(normalizer.normalize_name(name), 'arbors of bellhaven dayton')
        self.assertEqual(normalizer.normalize_name('The Pines-in-the-Valley'), 'pines-in-the-valley')
        self.assertEqual(normalizer.normalize_name('House of the Valley'), 'house of the valley')
        self.assertEqual(normalizer.normalize_name('Bellhaven at Dayton'),
                            normalizer.normalize_name('Bellhaven of Dayton'))
        self.assertEqual(normalizer.normalize_name('Atlas of Stratford'), 'atlas of stratford')
        self.assertIsNone(normalizer.normalize_name('The -'))

    def test_name_cleanup_is_saved_without_changing_source_names(self):
        self.c.execute("UPDATE crm_accounts SET name='Arbors at Bellhaven Dayton'")
        self.c.execute("UPDATE website_snapshots SET name='The Arbors at Bellhaven - Dayton'")
        self.c.commit()
        self.run_normalizer()
        for table in ('normalized_crm_accounts', 'normalized_website_locations'):
            self.assertEqual(self.c.execute(f'SELECT normalized_name FROM {table}').fetchone()[0],
                             'arbors of bellhaven dayton')
        self.assertEqual(self.c.execute('SELECT name FROM crm_accounts').fetchone()[0], 'Arbors at Bellhaven Dayton')
        self.assertEqual(self.c.execute('SELECT name FROM website_snapshots').fetchone()[0], 'The Arbors at Bellhaven - Dayton')

    def rows(self, table):
        return self.c.execute(f'SELECT * FROM {table} ORDER BY 1,2').fetchall()

    def assert_empty_batch(self):
        self.assertEqual(self.rows('normalized_crm_accounts'), [])
        self.assertEqual(self.rows('normalized_website_locations'), [])

    def test_saves_both_tables_and_preserves_all_original_fields(self):
        before = {t:self.rows(t) for t in ('crm_accounts','crm_contacts','website_snapshots')}
        summary = self.run_normalizer()
        self.assertEqual((summary['crm_count'],summary['website_count'],summary['reused']), (1,1,False))
        self.assertEqual(summary['website_snapshot_id'], 'SNAPSHOT')
        for t in before:
            self.assertEqual(before[t], self.rows(t))
        names = ','.join(normalizer.CRM_FIELDS)
        self.assertEqual(self.c.execute(f'SELECT {names} FROM normalized_crm_accounts').fetchall(), before['crm_accounts'])
        self.assertEqual(self.c.execute('SELECT normalized_name,normalized_street,normalized_city,normalized_state,address_fields_present FROM normalized_crm_accounts').fetchone(),
                         ('bellhaven healthcare center','123 w main st','dayton','oh',1))
        self.assertEqual(self.c.execute('SELECT normalization_status,crm_records_normalized,website_records_normalized,status FROM runs').fetchone(), ('complete',1,1,'running'))
        self.assertEqual(len(json.loads(self.c.execute('SELECT errors FROM runs').fetchone()[0])),1)

    def test_completed_batch_is_immutable_when_current_crm_changes(self):
        self.run_normalizer()
        before = self.rows('normalized_crm_accounts')
        self.c.execute("UPDATE crm_accounts SET name='Later name', outstanding_ar=0")
        self.c.commit()
        self.assertTrue(self.run_normalizer()['reused'])
        self.assertEqual(before,self.rows('normalized_crm_accounts'))

    def test_two_concurrent_calls_save_one_batch(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.run_normalizer(), range(2)))
        self.assertEqual(sorted(r['reused'] for r in results), [False,True])
        self.assertEqual(len(self.rows('normalized_crm_accounts')),1)
        self.assertEqual(len(self.rows('normalized_website_locations')),1)

    def test_incomplete_selected_run_never_falls_back_to_an_older_success(self):
        self.c.execute("INSERT INTO runs(run_id,started_at,status,locations_found,locations_saved) VALUES ('OLDER','earlier','complete',1,1)")
        self.c.execute("UPDATE runs SET scraping_status='incomplete' WHERE run_id='RUN'")
        self.c.commit()
        with self.assertRaisesRegex(ValueError,'No older snapshot'):
            self.run_normalizer()
        self.assert_empty_batch()

    def test_unknown_run_does_not_modify_existing_runs(self):
        before = self.rows('runs')
        with self.assertRaisesRegex(ValueError,'does not exist'):
            self.run_normalizer('UNKNOWN')
        self.assertEqual(before,self.rows('runs'))
        self.assert_empty_batch()

    def test_bad_snapshot_counts_record_failure_without_partial_batch(self):
        self.c.execute('UPDATE runs SET locations_saved=2')
        self.c.commit()
        with self.assertRaisesRegex(ValueError,'inconsistent records'):
            self.run_normalizer()
        self.assert_empty_batch()
        self.assertEqual(self.c.execute('SELECT normalization_status FROM runs').fetchone()[0],'failed')

    def test_failure_in_second_table_rolls_back_first_table_and_can_retry(self):
        self.c.executescript("""CREATE TRIGGER fail_website_insert
            BEFORE INSERT ON normalized_website_locations BEGIN
                SELECT RAISE(ABORT,'simulated insert failure');
            END;""")
        with self.assertRaisesRegex(sqlite3.IntegrityError,'simulated'):
            self.run_normalizer()
        self.assert_empty_batch()
        status, crm_count, website_count, errors = self.c.execute('SELECT normalization_status,crm_records_normalized,website_records_normalized,errors FROM runs').fetchone()
        self.assertEqual((status,crm_count,website_count),('failed',0,0))
        self.assertEqual([e['stage'] for e in json.loads(errors)],['scraping','normalization'])
        self.c.execute('DROP TRIGGER fail_website_insert')
        self.c.commit()
        self.assertFalse(self.run_normalizer()['reused'])
        self.assertEqual([e['stage'] for e in json.loads(self.c.execute('SELECT errors FROM runs').fetchone()[0])],['scraping'])

    def test_production_uses_same_tables_without_api_or_snapshot_option(self):
        self.c.close()
        production = self.path.with_name('production.sqlite')
        shutil.copyfile(self.path,production)
        self.assertEqual(normalizer.normalize_database(production,'RUN')['crm_count'],1)

    def test_empty_crm_records_a_clear_failure(self):
        self.c.execute('DELETE FROM crm_accounts')
        self.c.commit()
        with self.assertRaisesRegex(ValueError,'no CRM accounts'):
            self.run_normalizer()
        self.assert_empty_batch()
        self.assertEqual(self.c.execute('SELECT normalization_status FROM runs').fetchone()[0],'failed')

    def test_partial_or_already_used_batches_are_not_overwritten(self):
        self.c.execute("INSERT INTO normalized_crm_accounts(run_id,account_id,address_fields_present) VALUES ('RUN','A',0)")
        self.c.commit()
        with self.assertRaisesRegex(ValueError,'unfinished normalized batch'):
            self.run_normalizer()
        self.assertEqual(len(self.rows('normalized_crm_accounts')),1)
        self.c.execute('DELETE FROM normalized_crm_accounts')
        self.c.execute("UPDATE runs SET matching_status='complete'")
        self.c.commit()
        with self.assertRaisesRegex(ValueError,'Matching has already used'):
            self.run_normalizer()

    def test_missing_values_are_stored_as_null(self):
        self.c.execute('UPDATE crm_accounts SET name=NULL, billing_street=NULL')
        self.c.commit()
        self.assertEqual(self.run_normalizer()['missing_addresses'],1)
        self.assertEqual(self.c.execute('SELECT normalized_name,normalized_street,address_fields_present FROM normalized_crm_accounts').fetchone(),(None,None,0))

    def test_start_and_hardlink_are_protected(self):
        start=self.path.with_name('start.sqlite')
        shutil.copyfile(self.path,start)
        with self.assertRaisesRegex(ValueError,'protected'):
            normalizer.normalize_database(start,'RUN')
        alias=self.path.with_name('alias.sqlite')
        alias.hardlink_to(start)
        with patch.object(normalizer,'START_DATABASE',start):
            with self.assertRaisesRegex(ValueError,'protected'):
                normalizer.normalize_database(alias,'RUN')

    def test_cli_writes_database_without_creating_exports(self):
        script=self.root/'normalize_data.py'
        shutil.copyfile(normalizer.PROJECT_DIR/'normalize_data.py',script)
        before={p.relative_to(self.root) for p in self.root.rglob('*')}
        result=subprocess.run([sys.executable,'-B',str(script),'--database','demo','--run-id','RUN'],cwd=self.root,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('Saved normalized batch.',result.stdout)
        self.assertEqual(before,{p.relative_to(self.root) for p in self.root.rglob('*')})
        self.assertEqual(len(self.rows('normalized_crm_accounts')),1)
        missing_id=subprocess.run([sys.executable,'-B',str(script),'--database','demo'],cwd=self.root,capture_output=True,text=True)
        self.assertNotEqual(missing_id.returncode,0)
        self.assertIn('--run-id',missing_id.stderr)


if __name__ == '__main__':
    unittest.main()

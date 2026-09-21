"""Exercise the real three-stage pipeline with local HTML and temporary SQLite."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import normalize_data
import review_service as review
try:
    import scrape_website as scraper
    import run_pipeline as runner
except ModuleNotFoundError as error:
    if error.name not in ('requests', 'bs4'):
        raise
    scraper = runner = None


@unittest.skipIf(runner is None, 'Use the project .venv to install scraper dependencies')
class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'demo.sqlite'
        self.c = sqlite3.connect(self.path)
        self.c.row_factory = sqlite3.Row
        self.addCleanup(self.c.close)
        self.c.executescript((normalize_data.PROJECT_DIR / 'schema/working.sql').read_text())
        self.c.execute('PRAGMA user_version=3')
        self.c.execute("INSERT INTO crm_accounts(account_id,name,status,lifetime_revenue,outstanding_ar) VALUES ('P','Bellhaven Senior Living (Parent Account)','Active',0,0)")
        self.c.execute("""INSERT INTO crm_accounts
            (account_id,name,parent_id,parent_name,billing_street,billing_city,billing_state,billing_zip,status,lifetime_revenue,outstanding_ar)
            VALUES ('A','Old Name','P','Bellhaven Senior Living (Parent Account)','123 Main St','Town','OH','01234','Active',0,0)""")
        self.c.commit()
        self.base = scraper.BASE_URL
        self.url = self.base + 'communities/house'
        self.pages = {
            self.base: '<h1>Bellhaven</h1><a href="/communities">Communities</a><p>We serve 1 communities</p>',
            scraper.DIRECTORY_URL: '<h1>Communities</h1><p>Page 1 of 1 · 1 communities listed</p><a href="/communities/house">House</a>',
            self.url: '''<h1>Bellhaven House</h1><dl>
                <dt>Address</dt><dd>123 Main Street<br>Town, OH 01234</dd>
                <dt>Care Offerings</dt><dd><span class="badge">Assisted Living</span></dd>
                <dt>Phone</dt><dd>555-0100</dd><dt>Administrator</dt><dd>Jane</dd></dl>''',
        }
        self.network_calls = []
        def fetch(session, url):
            self.network_calls.append(url)
            return self.pages[url], '2026-09-20T12:00:00+00:00'
        self.fetch = patch.object(scraper, 'fetch_page', side_effect=fetch).start()
        self.addCleanup(patch.stopall)
        self.output = io.StringIO()
        self.stdout = redirect_stdout(self.output)
        self.stdout.__enter__()
        self.addCleanup(self.stdout.__exit__,None,None,None)

    def runs(self):
        return [dict(r) for r in self.c.execute('SELECT * FROM runs ORDER BY rowid')]

    def count(self, table):
        return self.c.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]

    def test_real_stages_run_once_in_order_with_one_shared_id(self):
        before = self.c.execute('SELECT * FROM crm_accounts').fetchall()
        stages = []
        real_normalize, real_match = runner.normalize_database, runner.match_database
        def normalize(path, run_id):
            row = self.runs()[0]
            self.assertEqual((row['status'],row['scraping_status'],row['normalization_status']),('running','complete','pending'))
            self.assertIsNone(row['finished_at'])
            self.assertEqual(row['website_snapshot_id'],run_id)
            stages.append('normalize')
            return real_normalize(path, run_id)
        def match(path, run_id):
            row = self.runs()[0]
            self.assertEqual((row['status'],row['normalization_status'],row['matching_status']),('running','complete','pending'))
            stages.append('match')
            return real_match(path,run_id)
        with patch.object(runner,'normalize_database',side_effect=normalize) as n, patch.object(runner,'match_database',side_effect=match) as m:
            result = runner.run_pipeline(self.path)
        self.assertEqual(stages,['normalize','match'])
        self.assertEqual(n.call_count,1)
        self.assertEqual(m.call_count,1)
        row = self.runs()[0]
        self.assertEqual(len(self.runs()),1)
        self.assertEqual(row['run_id'],result['run_id'])
        self.assertEqual(row['run_type'],'pipeline')
        self.assertEqual(row['status'],'complete')
        self.assertEqual(row['current_stage'],'complete')
        self.assertIsNotNone(row['finished_at'])
        for stage in ('scraping','normalization','matching'):
            self.assertEqual(row[stage+'_status'],'complete')
        self.assertEqual(self.network_calls,[self.base,scraper.DIRECTORY_URL,self.url])
        self.assertEqual(before,self.c.execute('SELECT * FROM crm_accounts').fetchall())
        self.assertEqual(self.count('proposals'),1)
        self.assertEqual(self.count('decisions'),0)
        self.assertEqual(self.count('change_history'),0)
        self.assertEqual(self.c.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_second_pipeline_gets_fresh_snapshot_but_reuses_proposal(self):
        first = runner.run_pipeline(self.path)
        second = runner.run_pipeline(self.path)
        self.assertNotEqual(first['run_id'],second['run_id'])
        self.assertEqual(self.count('website_snapshots'),2)
        self.assertEqual(self.count('proposals'),1)
        self.assertEqual(second['matching']['existing_proposals'],1)

    def test_submitted_rejection_is_not_reproposed_by_next_full_pipeline(self):
        runner.run_pipeline(self.path)
        proposal = review.unpack(self.c.execute('SELECT * FROM proposals').fetchone())
        pid = proposal['proposal_id']
        review.stage(self.path, [dict(proposal_id=pid, choice='rejected',
                                     version=review.version(proposal), revision=None)])
        decision = self.c.execute('SELECT * FROM decisions WHERE proposal_id=?', (pid,)).fetchone()
        review.submit(self.path, [dict(proposal_id=pid, revision=decision['decided_at'])])
        saved_decision = self.c.execute('SELECT * FROM decisions WHERE proposal_id=?', (pid,)).fetchone()
        self.assertIsNotNone(saved_decision['submitted_at'])

        second = runner.run_pipeline(self.path)

        self.assertEqual(second['matching']['new_proposals'], 0)
        self.assertEqual(second['matching']['decided_proposals_skipped'], 1)
        self.assertEqual(self.count('proposals'), 1)
        self.assertEqual(self.count('decisions'), 1)
        self.assertEqual(review.proposal_review_state(self.c, pid), 'decided')
        self.assertEqual(saved_decision, self.c.execute(
            'SELECT * FROM decisions WHERE proposal_id=?', (pid,)).fetchone())
        self.assertEqual(self.count('change_history'), 0)

    def test_failed_scrape_does_not_use_previous_success_or_call_later_steps(self):
        runner.run_pipeline(self.path)
        self.pages[self.url]='<h1>Broken detail page</h1>'
        with patch.object(runner,'normalize_database') as n, patch.object(runner,'match_database') as m:
            with self.assertRaises(runner.PipelineStopped):
                runner.run_pipeline(self.path)
            n.assert_not_called()
            m.assert_not_called()
        row = self.runs()[1]
        self.assertEqual(row['status'],'failed')
        self.assertEqual(row['scraping_status'],'failed')
        self.assertEqual(row['normalization_status'],'pending')
        self.assertEqual(row['matching_status'],'pending')
        self.assertEqual(self.count('normalized_website_locations'),1)

    def test_partial_scrape_is_incomplete_and_stops(self):
        self.pages[self.base]=self.pages[self.base].replace('serve 1','serve 2')
        self.pages[scraper.DIRECTORY_URL]=self.pages[scraper.DIRECTORY_URL].replace('1 communities','2 communities')+'<a href="/communities/broken">Broken</a>'
        self.pages[self.base+'communities/broken']='<h1>Broken</h1>'
        with self.assertRaises(runner.PipelineStopped):
            runner.run_pipeline(self.path)
        row = self.runs()[0]
        self.assertEqual((row['status'],row['scraping_status'],row['locations_saved']),('incomplete','incomplete',1))
        self.assertEqual(self.count('normalized_website_locations'),0)
        self.assertEqual(self.count('proposals'),0)

    def test_normalization_failure_marks_pipeline_failed_and_never_matches(self):
        self.c.executescript("CREATE TRIGGER fail_normalize BEFORE INSERT ON normalized_website_locations BEGIN SELECT RAISE(ABORT,'normalization failure'); END;")
        with patch.object(runner,'match_database') as match:
            with self.assertRaisesRegex(runner.PipelineStopped,'normalization failure'):
                runner.run_pipeline(self.path)
            match.assert_not_called()
        row = self.runs()[0]
        self.assertEqual((row['status'],row['current_stage'],row['normalization_status']),('failed','normalization','failed'))
        self.assertEqual(row['scraping_status'],'complete')
        self.assertEqual(row['matching_status'],'pending')
        self.assertEqual(self.count('normalized_crm_accounts'),0)

    def test_matching_failure_keeps_completed_input_but_no_partial_proposals(self):
        self.c.executescript("CREATE TRIGGER fail_matching BEFORE INSERT ON run_proposals BEGIN SELECT RAISE(ABORT,'matching failure'); END;")
        with self.assertRaisesRegex(runner.PipelineStopped,'matching failure'):
            runner.run_pipeline(self.path)
        row = self.runs()[0]
        self.assertEqual((row['status'],row['current_stage'],row['matching_status']),('failed','matching','failed'))
        self.assertEqual(row['normalization_status'],'complete')
        self.assertEqual(self.count('proposals'),0)
        self.assertEqual(self.count('normalized_website_locations'),1)

    def test_unrecorded_stage_success_is_not_treated_as_complete(self):
        with patch.object(runner,'normalize_database',return_value={}), patch.object(runner,'match_database') as match:
            with self.assertRaisesRegex(runner.PipelineStopped,'did not save a complete result'):
                runner.run_pipeline(self.path)
            match.assert_not_called()
        self.assertEqual(self.runs()[0]['status'],'failed')

    def test_interrupt_is_recorded_and_matching_never_runs(self):
        with patch.object(runner,'normalize_database',side_effect=KeyboardInterrupt), patch.object(runner,'match_database') as match:
            with self.assertRaises(KeyboardInterrupt):
                runner.run_pipeline(self.path)
            match.assert_not_called()
        row = self.runs()[0]
        self.assertEqual(row['status'],'failed')
        self.assertEqual(row['current_stage'],'normalization')
        self.assertIsNotNone(row['finished_at'])

    def test_scraper_interrupt_also_stops_pipeline(self):
        self.fetch.side_effect=KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            runner.run_pipeline(self.path)
        self.assertEqual(self.runs()[0]['status'],'failed')
        self.assertEqual(self.runs()[0]['normalization_status'],'pending')

    def test_overlapping_pipeline_is_rejected_before_network(self):
        active = runner.start_pipeline(self.path)
        with self.assertRaisesRegex(ValueError,'already running'):
            runner.run_pipeline(self.path)
        self.assertEqual(self.network_calls,[])
        self.assertEqual(self.runs()[0]['run_id'],active)
        self.assertEqual(self.runs()[0]['status'],'running')

    def test_wrong_schema_empty_crm_and_start_are_rejected_before_network(self):
        self.c.execute('PRAGMA user_version=2')
        with self.assertRaisesRegex(ValueError,'schema version 3'):
            runner.run_pipeline(self.path)
        self.c.execute('PRAGMA user_version=3')
        self.c.execute('DELETE FROM crm_accounts')
        self.c.commit()
        with self.assertRaisesRegex(ValueError,'no CRM accounts'):
            runner.run_pipeline(self.path)
        start = self.root/'start.sqlite'
        start.write_bytes(self.path.read_bytes())
        before = start.read_bytes()
        with self.assertRaisesRegex(ValueError,'protected'):
            runner.run_pipeline(start)
        self.assertEqual(start.read_bytes(),before)
        self.assertEqual(self.network_calls,[])
        self.assertEqual(self.count('runs'),0)

    def test_standalone_scraper_still_works_and_returns_its_id(self):
        result = scraper.scrape_database(self.path)
        self.assertEqual(result['status'],'complete')
        self.assertEqual(self.runs()[0]['run_type'],'scraper')
        self.assertEqual(self.runs()[0]['run_id'],result['run_id'])
        self.assertEqual(self.runs()[0]['status'],'complete')
        self.assertEqual(self.runs()[0]['normalization_status'],'pending')

    def test_scraper_refuses_to_overwrite_a_completed_pipeline(self):
        result = runner.run_pipeline(self.path)
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError,'pending pipeline'):
            scraper.scrape_database(self.path,run_id=result['run_id'])
        self.assertEqual(before,self.path.read_bytes())

    def test_cli_has_one_database_argument_and_failure_exit(self):
        with patch.object(runner,'DATABASES',{'demo':self.path}), patch('sys.argv',['run_pipeline.py','--database','demo']):
            runner.main()
        self.assertEqual(self.runs()[0]['status'],'complete')
        self.pages[self.url]='<h1>Broken</h1>'
        with patch.object(runner,'DATABASES',{'demo':self.path}), patch('sys.argv',['run_pipeline.py','--database','demo']):
            with self.assertRaises(SystemExit) as raised:
                runner.main()
        self.assertEqual(raised.exception.code,1)


if __name__ == '__main__':
    unittest.main()

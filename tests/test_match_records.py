"""Matching rules, proposal identity, and database behavior; temporary DBs only."""
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

import match_records as matcher
import normalize_data as normalizer
from refresh_proposal_copy import refresh
from proposal_copy import LEGACY_PARENT_SENTENCES, parent_sentence
from migrate_databases import migrate_database


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'demo.sqlite'
        self.c = sqlite3.connect(self.path)
        self.c.row_factory = sqlite3.Row
        self.addCleanup(self.c.close)
        self.c.executescript((normalizer.PROJECT_DIR / 'schema' / 'working.sql').read_text())
        self.c.execute('PRAGMA user_version=3')
        self.c.execute('PRAGMA foreign_keys=ON')
        self.account('P', name='Bellhaven Senior Living (Parent Account)', parent_id=None,
                     parent_name=None, billing_street=None, billing_city=None, billing_state=None)
        self.account('Q', name='Other (Parent Account)', parent_id=None, parent_name=None,
                     billing_street=None, billing_city=None, billing_state=None)

    def account(self, aid, **fields):
        values = dict(account_id=aid, name='Bellhaven House', parent_id='P', parent_name='Bellhaven Senior Living (Parent Account)',
                      billing_street='123 Main St', billing_city='Town', billing_state='OH', billing_zip='01234',
                      status='Active', lifetime_revenue=0, outstanding_ar=0, updated_at='2026-01-01',
                      phone='555-0100', care_type='Assisted Living')
        values.update(fields)
        self.c.execute(f'INSERT INTO crm_accounts ({",".join(values)}) VALUES ({",".join("?" for _ in values)})', tuple(values.values()))
        self.c.commit()

    def prepare(self, run='R', websites=None):
        websites = websites if websites is not None else [dict()]
        for index, fields in enumerate(websites):
            values = dict(snapshot_id=run, source_url=f'https://example.test/{index}', fetched_at=run,
                          name='Bellhaven House', street='123 Main Street', city='Town', state='OH', zip='01234',
                          care_offerings='["Assisted Living"]', phone='555-0100')
            values.update(fields)
            self.c.execute(f'INSERT INTO website_snapshots ({",".join(values)}) VALUES ({",".join("?" for _ in values)})', tuple(values.values()))
        self.c.execute("INSERT INTO runs(run_id,started_at,status,locations_found,locations_saved) VALUES (?,?,'complete',?,?)", (run, run, len(websites), len(websites)))
        self.c.commit()
        normalizer.normalize_database(self.path, run)

    def match(self, run='R'):
        return matcher.match_database(self.path, run)

    def proposals(self, classification=None):
        rows = self.c.execute('SELECT * FROM proposals' + (' WHERE classification=?' if classification else ''),
                              (classification,) if classification else ()).fetchall()
        out = []
        for row in rows:
            p = dict(row)
            for field in ('proposed_changes', 'supporting_evidence'):
                p[field] = json.loads(p[field])
            out.append(p)
        return out

    def decide(self, pid, choice='rejected', submitted=True, operations=None):
        did = 'D' + pid
        self.c.execute('INSERT INTO decisions(decision_id,proposal_id,choice,approved_changes,decided_at,submitted_at) VALUES (?,?,?,?,?,?)',
                       (did, pid, choice, json.dumps(operations or []), 'date', 'date' if submitted else None))
        self.c.commit()
        return did

    def test_updated_name_rules_supersede_old_rename_without_losing_parent_correction(self):
        self.account('A', name='Arbors at Bellhaven Dayton', parent_id='Q')
        website = [dict(name='The Arbors at Bellhaven - Dayton')]
        # Reproduce a saved batch made before leading articles/dashes were ignored.
        with patch.object(normalizer, 'normalize_name', normalizer.normalize_text):
            self.prepare(websites=website)
        self.match()
        old = self.proposals('name_parent_correction')[0]
        self.assertEqual(matcher.proposal_review_state(self.c, old['proposal_id']), 'superseded')
        with self.c:
            self.c.execute('BEGIN IMMEDIATE')
            with self.assertRaisesRegex(ValueError, 'superseded'):
                matcher.assert_proposal_applicable(self.c, old['proposal_id'])
        self.prepare('R2', websites=website); self.match('R2')
        new = self.proposals('parent_correction')[0]
        self.assertNotIn('name', new['proposed_changes'][0]['values'])
        self.assertEqual(matcher.proposal_review_state(self.c, new['proposal_id']), 'ready')
        self.assertEqual(self.proposals('name_parent_correction')[0], old)
        self.assertEqual(self.c.execute("SELECT name FROM crm_accounts WHERE account_id='A'").fetchone()[0],
                         'Arbors at Bellhaven Dayton')
        self.decide(old['proposal_id'])
        self.assertEqual(matcher.proposal_review_state(self.c, old['proposal_id']), 'decided')

    def test_equivalent_names_auto_complete_without_rename(self):
        self.account('A', name='Arbors at Bellhaven Dayton')
        self.prepare(websites=[dict(name='The Arbors at Bellhaven - Dayton')]); self.match()
        p = self.proposals()[0]
        self.assertEqual(p['classification'], 'confident_match')
        self.assertEqual(p['proposed_changes'], [])
        self.assertEqual(matcher.proposal_review_state(self.c, p['proposal_id']), 'decided')

    def test_at_of_variation_retires_old_rename_and_becomes_automatic_match(self):
        self.account('A', name='Bellhaven of Sycamore Ridge')
        website = [dict(name='Bellhaven at Sycamore Ridge')]
        with patch.object(normalizer, 'normalize_name', normalizer.normalize_text):
            self.prepare(websites=website)
        self.match()
        old = self.proposals('name_correction')[0]
        self.assertEqual(matcher.proposal_review_state(self.c, old['proposal_id']), 'superseded')
        self.prepare('R2', websites=website); self.match('R2')
        new = self.proposals('confident_match')[0]
        self.assertEqual(new['proposed_changes'], [])
        self.assertEqual(matcher.proposal_review_state(self.c, new['proposal_id']), 'decided')
        self.assertEqual(self.c.execute("SELECT name FROM crm_accounts WHERE account_id='A'").fetchone()[0],
                         'Bellhaven of Sycamore Ridge')

    def legacy_address_proposal(self):
        self.account('A', billing_street='999 Old Rd')
        self.prepare(); self.match()
        p=self.proposals('address_correction')[0]
        changes=p['proposed_changes']
        changes[0]['values'].update(billing_city='Town', billing_state='OH', billing_zip='01234')
        key=matcher.proposal_key(dict(target='A',changes=changes))
        self.c.execute('UPDATE proposals SET proposed_changes=?,proposal_key=? WHERE proposal_id=?',
                       (json.dumps(changes),key,p['proposal_id']))
        self.c.commit()
        return p['proposal_id']

    def test_legacy_pending_address_card_is_replaced_by_minimal_update(self):
        old=self.legacy_address_proposal()
        self.assertEqual(matcher.proposal_review_state(self.c,old),'superseded')
        self.prepare('R2');self.match('R2')
        ready=[p for p in self.proposals() if matcher.proposal_review_state(self.c,p['proposal_id'])=='ready']
        self.assertEqual(len(ready),1)
        self.assertEqual(ready[0]['proposed_changes'][0]['values'],{'billing_street':'123 Main Street'})

    def test_legacy_rejected_address_card_stays_decided_when_redundant_fields_removed(self):
        old=self.legacy_address_proposal();self.decide(old)
        self.prepare('R2');summary=self.match('R2')
        self.assertEqual(summary['decided_proposals_skipped'],1)
        self.assertEqual(summary['new_proposals'],0)
        self.assertEqual(len(self.proposals()),1)
        self.assertEqual(matcher.proposal_review_state(self.c,old),'decided')

    def test_parent_evidence_distinguishes_missing_from_different_in_every_path(self):
        paths = [
            ('parent_correction', {}),
            ('name_parent_correction', {'name': 'Old House'}),
            ('address_parent_correction', {'billing_street': '999 Old Rd'}),
            ('chow_address_match', {'lifetime_revenue': 100, 'outstanding_ar': 1}),
            ('chow_name_match', {'billing_street': '999 Old Rd', 'lifetime_revenue': 100, 'outstanding_ar': 1}),
        ]
        expected, websites = {}, []
        for parent_id in (None, 'Q'):
            for classification, fields in paths:
                i = len(websites)
                aid = f'A{i}'
                self.account(aid, **dict(fields, name=fields.get('name', 'House') + str(i),
                                        billing_city=f'Town{i}', parent_id=parent_id,
                                        parent_name=None))
                websites.append(dict(name=f'House{i}', city=f'Town{i}', source_url=f'https://example.test/{i}'))
                expected[aid] = (classification, 'CRM parent is missing.' if parent_id is None
                                 else 'CRM parent differs from Bellhaven.')
        self.prepare(websites=websites)
        self.match()
        self.assertEqual(len(self.proposals()), len(expected))
        for p in self.proposals():
            classification, sentence = expected[p['account_id']]
            with self.subTest(account=p['account_id']):
                self.assertEqual(p['classification'], classification)
                self.assertIn(sentence, p['supporting_evidence']['bullets'])
                self.assertFalse(LEGACY_PARENT_SENTENCES.intersection(p['supporting_evidence']['bullets']))
                if classification == 'name_parent_correction':
                    self.assertIn(sentence, p['explanation'])

    def test_parent_copy_refresh_preserves_decisions_operations_and_snapshots(self):
        self.account('A', name='Old House', parent_id=None, parent_name='Stale display name')
        self.prepare(); self.match()
        p = self.proposals('name_parent_correction')[0]
        evidence = p['supporting_evidence']
        evidence['bullets'] = list(LEGACY_PARENT_SENTENCES)
        with self.c:
            self.c.execute('UPDATE proposals SET supporting_evidence=?, explanation=? WHERE proposal_id=?',
                           (json.dumps(evidence), 'The address matches, but the name and parent differ.', p['proposal_id']))
        self.decide(p['proposal_id'], submitted=False)
        before = self.proposals()[0]
        tables = ('decisions', 'crm_accounts', 'crm_contacts', 'change_history')
        saved = {table: list(self.c.execute(f'SELECT * FROM {table}')) for table in tables}
        count, backup = refresh(self.path)
        self.assertEqual(count, 1)
        self.assertTrue(backup.is_file())
        after = self.proposals()[0]
        self.assertEqual(after['supporting_evidence']['bullets'], ['CRM parent is missing.'] * 2)
        self.assertIn('CRM parent is missing.', after['explanation'])
        for key in before:
            if key not in ('supporting_evidence', 'explanation'):
                self.assertEqual(after[key], before[key])
        for key in evidence:
            if key != 'bullets':
                self.assertEqual(after['supporting_evidence'][key], evidence[key])
        for table in tables:
            self.assertEqual(list(self.c.execute(f'SELECT * FROM {table}')), saved[table])
        self.assertEqual(refresh(self.path), (0, None))
        self.assertEqual(parent_sentence({'parent_id': 'Q', 'parent_name': None}), 'CRM parent differs from Bellhaven.')
        self.assertEqual(parent_sentence({'parent_id': ' ', 'parent_name': 'Stale'}), 'CRM parent is missing.')

    def test_all_normal_matching_cases_and_chow(self):
        cases = [
            ('confident_match', {}, {}),
            ('name_correction', {'name':'Old House'}, {}),
            ('parent_correction', {'parent_id':'Q', 'parent_name':'Other'}, {}),
            ('name_parent_correction', {'name':'Old House', 'parent_id':'Q'}, {}),
            ('address_correction', {'billing_street':'999 Old Rd'}, {}),
            ('address_parent_correction', {'billing_street':'999 Old Rd', 'parent_id':'Q'}, {}),
            ('chow_address_match', {'parent_id':'Q','lifetime_revenue':100,'outstanding_ar':1}, {}),
            ('chow_name_match', {'parent_id':'Q','billing_street':'999 Old Rd','lifetime_revenue':100,'outstanding_ar':1}, {}),
        ]
        for i, (expected, fields, web) in enumerate(cases):
            with self.subTest(expected=expected):
                fields['billing_city'] = f'Town{i}'
                fields['name'] = fields.get('name', 'Bellhaven House') + str(i)
                self.account(f'A{i}', **fields)
                web.update(city=f'Town{i}', source_url=f'https://example.test/{i}')
        self.prepare(websites=[dict(name=f'Bellhaven House{i}', city=f'Town{i}', source_url=f'https://example.test/{i}') for i in range(len(cases))])
        before = list(self.c.execute('SELECT * FROM crm_accounts'))
        result = self.match()
        self.assertEqual(result['new_proposals'], len(cases))
        self.assertEqual(sorted(p['classification'] for p in self.proposals()), sorted(c[0] for c in cases))
        self.assertEqual(before, list(self.c.execute('SELECT * FROM crm_accounts')))
        for p in self.proposals():
            self.assertTrue(p['title'])
            self.assertLessEqual(len(p['supporting_evidence']['bullets']), 4)
            if p['classification'].startswith('chow_'):
                create, update = p['proposed_changes']
                self.assertEqual(create['values']['lifetime_revenue'], 0)
                self.assertEqual(create['values']['outstanding_ar'], 0)
                self.assertEqual(list(update['values']), ['chow_current_account'])
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM decisions').fetchone()[0], 1)

    def test_financial_boundaries_and_missing_information(self):
        for revenue, ar, expected in [(0,0,'direct'), (10,0,'direct'), (0,10,'direct'), (10,2,'chow'),
                                       (None,2,'unknown'), (10,None,'unknown'), (0,None,'direct'),
                                       (None,0,'direct'), ('bad',3,'unknown'), (-1,2,'unknown')]:
            with self.subTest(revenue=revenue, ar=ar):
                self.assertEqual(matcher.financial_rule(dict(lifetime_revenue=revenue,outstanding_ar=ar))[0], expected)
        self.account('A', parent_id='Q', lifetime_revenue=None, outstanding_ar=2)
        self.prepare()
        summary = self.match()
        self.assertEqual(self.proposals(), [])
        self.assertEqual(summary['issues'][0]['code'], 'insufficient_financial_data')

    def test_zip_only_is_ignored_and_address_update_preserves_zip_when_missing(self):
        self.account('A', billing_zip='99999')
        self.prepare()
        self.match()
        self.assertEqual(self.proposals()[0]['classification'], 'confident_match')
        self.assertEqual(self.proposals()[0]['proposed_changes'], [])
        self.c.execute("UPDATE crm_accounts SET billing_street='9 Other Rd'")
        self.c.commit()
        self.prepare('R2', [dict(zip=None)])
        self.match('R2')
        self.assertNotIn('billing_zip', self.proposals('address_correction')[0]['proposed_changes'][0]['values'])

    def test_creates_and_absence_have_real_evidence_and_stable_note(self):
        self.account('A', name='No Longer Listed', billing_street='9 Old Rd')
        self.prepare()
        self.match()
        self.assertEqual({p['classification'] for p in self.proposals()}, {'create_account','absent_from_website'})
        create = self.proposals('create_account')[0]
        self.assertEqual(create['proposed_changes'][0]['values']['billing_zip'], '01234')
        self.assertIsNone(create['account_id'])
        absent = self.proposals('absent_from_website')[0]
        self.assertEqual(absent['proposed_changes'][0]['values'], {'status':'Inactive'})
        self.assertEqual(absent['proposed_changes'][1]['action'], 'append_note')

    def test_incomplete_website_withholds_creation_and_all_absence(self):
        self.account('A', name='Another facility')
        self.prepare(websites=[dict(street=None)])
        result = self.match()
        self.assertEqual(self.proposals(), [])
        self.assertEqual({i['code'] for i in result['issues']}, {'incomplete_website_record','absence_check_withheld'})

    def test_different_names_same_address_get_independent_cards(self):
        self.account('A')
        self.account('B', name='Former Operator', parent_id='Q')
        self.account('C', name='Another Operator', parent_id='Q')
        self.prepare()
        self.match()
        self.assertEqual(len(self.proposals()), 3)
        for p in self.proposals():
            self.assertIsNone(p['depends_on_proposal_id'])
            self.assertEqual(len(p['supporting_evidence']['bullets']), 4)
            self.assertIn('3 CRM', p['supporting_evidence']['bullets'][3])

    def test_duplicate_cards_block_then_reveal_survivor_without_matching_again(self):
        self.account('A', parent_id='Q')
        self.account('B', parent_id='Q')
        self.prepare()
        self.match()
        group = self.proposals('duplicate_resolution')[0]
        corrections = {p['account_id']:p for p in self.proposals('parent_correction')}
        for p in corrections.values():
            self.assertEqual(p['depends_on_proposal_id'], group['proposal_id'])
            self.assertEqual(matcher.proposal_review_state(self.c,p['proposal_id']), 'blocked')
        did = self.decide(group['proposal_id'], 'approved', operations=[dict(action='update',account_id='B',values={'status':'Inactive','duplicate_of_account':'A'})])
        self.assertEqual(matcher.proposal_review_state(self.c,corrections['A']['proposal_id']), 'blocked')
        # Simulate the future approval backend's single transaction.
        self.c.execute("UPDATE crm_accounts SET status='Inactive',duplicate_of_account='A' WHERE account_id='B'")
        self.c.execute("INSERT INTO change_history(change_id,decision_id,attempted_at,result,after_values) VALUES ('H',?,'date','succeeded','[]')", (did,))
        self.c.commit()
        self.assertEqual(matcher.proposal_review_state(self.c,corrections['A']['proposal_id']), 'ready')
        self.assertEqual(matcher.proposal_review_state(self.c,corrections['B']['proposal_id']), 'superseded')
        # New day excludes B; A's existing correction is reused.
        self.prepare('R2')
        result = self.match('R2')
        self.assertEqual(result['new_proposals'], 0)
        self.assertEqual(len(self.proposals('duplicate_resolution')),1)

    def test_keep_both_is_remembered_and_not_confused_with_rejection(self):
        self.account('A', parent_id='Q')
        self.account('B', parent_id='Q')
        self.prepare()
        self.match()
        group = self.proposals('duplicate_resolution')[0]
        child = self.proposals('parent_correction')[0]
        self.decide(group['proposal_id'], 'reviewed')
        self.assertEqual(matcher.proposal_review_state(self.c,child['proposal_id']), 'ready')
        self.prepare('R2')
        result = self.match('R2')
        self.assertEqual(result['decided_proposals_skipped'],1)
        self.assertEqual(len(self.proposals()),3)

    def test_duplicate_groups_can_have_three_accounts_and_same_identity_key(self):
        for aid in ['C','B','A']:
            self.account(aid, parent_id='Q')
        self.prepare()
        self.match()
        p = self.proposals('duplicate_resolution')[0]
        self.assertEqual(p['proposed_changes'][0]['account_ids'], ['A','B','C'])
        self.assertEqual(len(self.proposals()),4)
        self.prepare('R2')
        self.match('R2')
        self.assertEqual(len(self.proposals()),4)

    def test_pending_and_decided_reuse_excludes_changed_evidence_and_timestamps(self):
        self.account('A', name='Old')
        self.prepare()
        self.match()
        p = self.proposals()[0]
        self.decide(p['proposal_id'], submitted=False)
        self.prepare('R2', [dict(phone='different phone')])
        result = self.match('R2')
        self.assertEqual(result['existing_proposals'], 1)
        self.c.execute('UPDATE decisions SET submitted_at=?', ('submitted',))
        self.c.commit()
        self.prepare('R3', [dict(phone='another phone')])
        result = self.match('R3')
        self.assertEqual(result['decided_proposals_skipped'],1)
        self.assertEqual(len(self.proposals()),1)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM run_proposals').fetchone()[0],3)
        # Actually changed proposed name is a new suggestion.
        self.prepare('R4', [dict(name='New website name')])
        self.assertEqual(self.match('R4')['new_proposals'],1)

    def test_chow_links_are_followed_and_old_records_never_marked_absent(self):
        self.account('A', chow_current_account='B', name='Old Name')
        self.account('B', duplicate_of_account='C')
        self.account('C')
        self.prepare()
        self.match()
        self.assertEqual([p['account_id'] for p in self.proposals()], ['C'])

    def test_bad_links_fail_without_proposals(self):
        self.account('A', chow_current_account='B')
        self.account('B', duplicate_of_account='A')
        self.prepare()
        with self.assertRaisesRegex(ValueError,'Circular'):
            self.match()
        self.assertEqual(self.proposals(),[])
        self.assertEqual(self.c.execute('SELECT matching_status FROM runs').fetchone()[0], 'failed')

    def test_failed_insert_rolls_back_proposals_links_and_allows_retry(self):
        self.account('A', name='Old')
        self.prepare()
        self.c.executescript("CREATE TRIGGER fail_link BEFORE INSERT ON run_proposals BEGIN SELECT RAISE(ABORT,'test failure'); END;")
        with self.assertRaisesRegex(sqlite3.IntegrityError,'test failure'):
            self.match()
        self.assertEqual(self.proposals(), [])
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM run_proposals').fetchone()[0],0)
        self.c.execute('DROP TRIGGER fail_link')
        self.c.commit()
        self.assertEqual(self.match()['new_proposals'],1)
        self.assertEqual(json.loads(self.c.execute('SELECT errors FROM runs').fetchone()[0]), [])

    def test_concurrent_retries_generate_one_batch(self):
        self.account('A', name='Old')
        self.prepare()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _:self.match(), range(2)))
        self.assertEqual(sorted(r['reused'] for r in results), [False,True])
        self.assertEqual(len(self.proposals()),1)

    def test_stale_crm_and_failed_scrape_never_generate_from_old_data(self):
        self.account('A')
        self.prepare()
        self.c.execute("UPDATE crm_accounts SET outstanding_ar=3 WHERE account_id='A'")
        self.c.commit()
        with self.assertRaisesRegex(ValueError,'CRM changed'):
            self.match()
        self.assertEqual(self.proposals(), [])
        self.c.execute("UPDATE runs SET scraping_status='failed'")
        self.c.commit()
        with self.assertRaisesRegex(ValueError,'complete scraping'):
            self.match()

    def test_multiple_websites_share_address_but_cannot_make_conflicting_changes_silently(self):
        self.account('A', name='Old')
        self.prepare(websites=[dict(name='First'),dict(name='Second')])
        self.match()
        self.assertEqual(len(self.proposals()),2)
        for p in self.proposals():
            self.assertIn('2 website',p['supporting_evidence']['bullets'][3])
            self.assertEqual(p['supporting_evidence']['preconditions']['A']['name'], 'Old')

    def test_start_protection_and_production_uses_same_local_logic(self):
        self.account('A')
        self.prepare()
        production = self.path.with_name('production.sqlite')
        self.c.commit()
        shutil.copyfile(self.path,production)
        self.assertEqual(matcher.match_database(production,'R')['new_proposals'],1)
        start = self.root / 'start.sqlite'
        shutil.copyfile(self.path,start)
        before = start.read_bytes()
        with self.assertRaisesRegex(ValueError,'protected'):
            matcher.match_database(start,'R')
        self.assertEqual(before,start.read_bytes())

    def test_real_cli_on_temporary_project(self):
        self.account('A', name='Old')
        self.prepare()
        project = self.root / 'project'
        (project / 'data').mkdir(parents=True)
        for name in ('match_records.py','normalize_data.py','supplemental_updates.py','proposal_copy.py','duplicate_resolution.py'):
            shutil.copyfile(normalizer.PROJECT_DIR / name,project / name)
        self.c.commit()
        shutil.copyfile(self.path,project / 'data' / 'demo.sqlite')
        result = subprocess.run([sys.executable,'-B',str(project / 'match_records.py'),'--database','demo','--run-id','R'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('new_proposals: 1',result.stdout)

    def test_v2_migration_preserves_proposals_and_does_not_reset_stage_metadata(self):
        path = self.root / 'v2.sqlite'
        sql = (normalizer.PROJECT_DIR / 'schema' / 'working.sql').read_text()
        sql = sql.replace(",\n    title TEXT NOT NULL DEFAULT '',\n    depends_on_proposal_id TEXT REFERENCES proposals(proposal_id)", '')
        sql = sql.replace('CREATE INDEX IF NOT EXISTS proposals_dependency ON proposals(depends_on_proposal_id);','')
        with closing(sqlite3.connect(path)) as c:
            c.executescript(sql)
            c.execute('PRAGMA user_version=2')
            c.execute("INSERT INTO runs(run_id,started_at,status,normalization_status,matching_status) VALUES ('OLD','date','complete','complete','complete')")
            c.execute("INSERT INTO proposals(proposal_id,proposal_key,classification,explanation,created_at) VALUES ('OLD','key','confident_match','text','date')")
            c.commit()
        backup = migrate_database(path,'demo',self.root / 'backups')
        with closing(sqlite3.connect(path)) as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],4)
            self.assertEqual(c.execute('SELECT normalization_status,matching_status FROM runs').fetchone(),('complete','complete'))
            self.assertEqual(c.execute('SELECT title,depends_on_proposal_id FROM proposals').fetchone(),('',None))
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.assertTrue(backup.exists())
        self.assertIsNone(migrate_database(path,'demo',self.root / 'backups'))

    def test_applicability_guard_blocks_dependencies_and_conflicting_changes(self):
        self.account('A', name='Old')
        self.prepare(websites=[dict(name='First'),dict(name='Second')])
        self.match()
        proposals = self.proposals('name_correction')
        with self.assertRaisesRegex(ValueError, 'inside the approval transaction'):
            matcher.assert_proposal_applicable(self.c, proposals[0]['proposal_id'])
        self.c.execute('BEGIN IMMEDIATE')
        matcher.assert_proposal_applicable(self.c, proposals[0]['proposal_id'])
        self.c.execute("UPDATE crm_accounts SET name='First' WHERE account_id='A'")
        with self.assertRaisesRegex(ValueError,'stale'):
            matcher.assert_proposal_applicable(self.c, proposals[1]['proposal_id'])
        self.c.rollback()

    def test_unreviewed_pending_evidence_refreshes_but_key_and_id_remain(self):
        self.account('A', name='Old', parent_id='Q', lifetime_revenue=10)
        self.prepare()
        self.match()
        old = self.proposals()[0]
        self.c.execute("UPDATE crm_accounts SET lifetime_revenue=20 WHERE account_id='A'")
        self.c.commit()
        self.prepare('R2')
        result = self.match('R2')
        new = self.proposals()[0]
        self.assertEqual(result['existing_proposals'],1)
        self.assertEqual(old['proposal_id'],new['proposal_id'])
        self.assertEqual(new['supporting_evidence']['crm']['lifetime_revenue'],20)
        self.c.execute('BEGIN IMMEDIATE')
        matcher.assert_proposal_applicable(self.c,new['proposal_id'])
        self.c.rollback()

    def test_new_third_duplicate_supersedes_old_question_and_reblocks_children(self):
        self.account('A', parent_id='Q')
        self.account('B', parent_id='Q')
        self.prepare()
        self.match()
        old_group = self.proposals('duplicate_resolution')[0]
        child = self.proposals('parent_correction')[0]
        self.c.execute('BEGIN IMMEDIATE')
        with self.assertRaisesRegex(ValueError,'blocked'):
            matcher.assert_proposal_applicable(self.c,child['proposal_id'])
        self.c.rollback()
        self.account('C', parent_id='Q')
        self.prepare('R2')
        self.match('R2')
        self.assertEqual(matcher.proposal_review_state(self.c,old_group['proposal_id']),'superseded')
        self.assertNotEqual(self.c.execute('SELECT depends_on_proposal_id FROM proposals WHERE proposal_id=?',(child['proposal_id'],)).fetchone()[0],old_group['proposal_id'])

    def test_rejected_duplicate_question_does_not_release_updates(self):
        self.account('A', parent_id='Q')
        self.account('B', parent_id='Q')
        self.prepare()
        self.match()
        self.decide(self.proposals('duplicate_resolution')[0]['proposal_id'])
        for p in self.proposals('parent_correction'):
            self.assertEqual(matcher.proposal_review_state(self.c,p['proposal_id']),'blocked')

    def test_two_new_runs_share_one_pending_proposal(self):
        self.account('A', name='Old')
        self.prepare('R1')
        self.prepare('R2')
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.match,['R1','R2']))
        self.assertEqual(sum(r['new_proposals'] for r in results),1)
        self.assertEqual(sum(r['existing_proposals'] for r in results),1)
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM run_proposals').fetchone()[0],2)

    def test_new_duplicate_blocks_older_correction_with_a_different_key(self):
        self.account('A', parent_id='Q')
        self.prepare()
        self.match()
        old = self.proposals('parent_correction')[0]
        self.account('B', parent_id='Q')
        self.prepare('R2', [dict(name='New website name')])
        self.match('R2')
        self.assertEqual(len(self.proposals('name_parent_correction')),2)
        self.assertIn(matcher.proposal_review_state(self.c,old['proposal_id']),('blocked','superseded'))


if __name__ == '__main__':
    unittest.main()

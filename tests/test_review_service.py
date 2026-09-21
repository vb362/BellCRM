"""Review transactions and HTTP boundaries use isolated SQLite files only."""
import json
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch

import review_service as review
import server
import test_match_records as fixtures


class ReviewTests(unittest.TestCase):
    setUp = fixtures.MatchingTests.setUp
    account = fixtures.MatchingTests.account
    prepare = fixtures.MatchingTests.prepare
    match = fixtures.MatchingTests.match
    proposals = fixtures.MatchingTests.proposals

    def build(self, **fields):
        self.account('A', **fields)
        self.prepare(); self.match()
        return self.proposals()[0]

    def item(self, p, choice='approved', **extra):
        return dict(proposal_id=p['proposal_id'], version=review.version(p), revision=None, choice=choice, **extra)

    def submit_all(self):
        items=[dict(proposal_id=r['proposal_id'],revision=r['decided_at']) for r in self.c.execute('SELECT * FROM decisions WHERE submitted_at IS NULL')]
        review.submit(self.path, items)
        return items

    def test_stage_submit_retry_and_persistent_history(self):
        p=self.build(name='Old House')
        review.stage(self.path,[self.item(p)])
        self.assertEqual('Old House',self.c.execute("SELECT name FROM crm_accounts WHERE account_id='A'").fetchone()[0])
        items=self.submit_all()
        self.assertEqual('Bellhaven House',self.c.execute("SELECT name FROM crm_accounts WHERE account_id='A'").fetchone()[0])
        self.assertEqual(0,review.submit(self.path,items))
        self.assertEqual(1,self.c.execute('SELECT count(*) FROM change_history').fetchone()[0])
        self.assertTrue(review.read_state(self.path)['proposals'][0]['decision']['submitted_at'])
        self.prepare('R2');self.match('R2')
        self.assertNotIn(p['proposal_id'],[r['proposal_id'] for r in review.read_state(self.path)['proposals'] if r['review_state']=='ready'])

    def test_address_update_preserves_equivalent_fields_and_supplies_normalized_comparisons(self):
        self.account('A', name='The Bellhaven at House', billing_street='805 Colegate Drive',
                     billing_city='Old Town', billing_state='OH')
        self.prepare(websites=[dict(name='Bellhaven of House', street='805 Colegate Dr', city='New Town', state='oh')])
        self.match()
        p=self.proposals('address_correction')[0]
        self.assertEqual(p['proposed_changes'][0]['values'], {'billing_city':'New Town'})
        served=next(p for p in review.read_state(self.path)['proposals'] if p['classification']=='address_correction')
        for field in ('name','street','state'):
            self.assertEqual(served['comparison_values']['crm'][field],served['comparison_values']['website'][field])
        self.assertEqual(served['comparison_values']['proposed'], {'city':'new town'})
        review.stage(self.path,[self.item(p)]);self.submit_all()
        row=self.c.execute("SELECT name,billing_street,billing_city,billing_state FROM crm_accounts WHERE account_id='A'").fetchone()
        self.assertEqual(tuple(row),('The Bellhaven at House','805 Colegate Drive','New Town','OH'))

    def test_rejection_suppressed_on_next_run(self):
        p=self.build(name='Old House')
        review.stage(self.path,[self.item(p,'rejected')]);self.submit_all()
        self.prepare('R2');self.match('R2')
        self.assertEqual(1,len(self.proposals()))
        self.assertEqual('decided',review.read_state(self.path)['proposals'][0]['review_state'])
        self.assertEqual(0,self.c.execute('SELECT count(*) FROM change_history').fetchone()[0])

    def test_conflicting_and_stale_decisions_roll_back_batch(self):
        self.account('A',name='Old House')
        self.account('B',name='Second Old',billing_city='Other Town')
        self.prepare(websites=[{},dict(city='Other Town',name='Second House')]);self.match()
        ps=self.proposals()
        review.stage(self.path,[self.item(p) for p in ps])
        self.c.execute("UPDATE crm_accounts SET lifetime_revenue=99 WHERE account_id='B'");self.c.commit()
        with self.assertRaisesRegex(ValueError,'stale'):self.submit_all()
        self.assertEqual('Old House',self.c.execute("SELECT name FROM crm_accounts WHERE account_id='A'").fetchone()[0])
        self.assertEqual(0,self.c.execute('SELECT count(*) FROM change_history').fetchone()[0])
        self.assertEqual(0,self.c.execute('SELECT count(*) FROM decisions WHERE submitted_at IS NOT NULL').fetchone()[0])

    def test_chow_preserves_old_account_except_link(self):
        p=self.build(parent_id='Q',parent_name='Other',lifetime_revenue=100,outstanding_ar=10)
        before=dict(self.c.execute("SELECT * FROM crm_accounts WHERE account_id='A'").fetchone())
        review.stage(self.path,[self.item(p)]);self.submit_all()
        after=dict(self.c.execute("SELECT * FROM crm_accounts WHERE account_id='A'").fetchone())
        new=after.pop('chow_current_account');before.pop('chow_current_account')
        self.assertEqual(before,after)
        row=self.c.execute('SELECT * FROM crm_accounts WHERE account_id=?',(new,)).fetchone()
        self.assertEqual('P',row['parent_id']);self.assertEqual(0,row['outstanding_ar'])
        self.assertEqual(2,len(json.loads(self.c.execute('SELECT after_values FROM change_history').fetchone()[0])))

    def duplicates(self):
        self.account('A',name='Old House');self.account('B',name='Old House')
        self.prepare();self.match()
        return next(p for p in self.proposals() if p['classification']=='duplicate_resolution')

    def test_duplicate_atomic_resolution_reveals_only_survivor(self):
        p=self.duplicates()
        self.assertEqual(1,len(review.read_state(self.path)['proposals']))
        child=next(p for p in self.proposals() if p['account_id']=='B')
        with self.assertRaises(ValueError):review.stage(self.path,[self.item(child)])
        review.stage(self.path,[self.item(p,survivor='A',note='Keep A for its billing history.')])
        self.assertEqual(1,len(review.read_state(self.path)['proposals']))
        self.submit_all()
        loser=self.c.execute("SELECT * FROM crm_accounts WHERE account_id='B'").fetchone()
        self.assertEqual('Inactive',loser['status']);self.assertEqual('A',loser['duplicate_of_account'])
        self.assertEqual('Keep A for its billing history.',loser['note'])
        dismissed=self.c.execute('SELECT * FROM decisions WHERE proposal_id=?',(child['proposal_id'],)).fetchone()
        self.assertEqual('rejected',dismissed['choice']);self.assertTrue(dismissed['submitted_at'])
        survivor=next(p for p in review.read_state(self.path)['proposals'] if p['review_state']=='ready')
        self.assertEqual('A',survivor['account_id'])
        review.stage(self.path,[self.item(survivor)]);self.submit_all()
        self.assertEqual('Bellhaven House',self.c.execute("SELECT name FROM crm_accounts WHERE account_id='A'").fetchone()[0])
        self.assertEqual('Old House',self.c.execute("SELECT name FROM crm_accounts WHERE account_id='B'").fetchone()[0])

    def test_duplicate_keep_both_and_rejection_are_different(self):
        p=self.duplicates()
        review.stage(self.path,[self.item(p,'reviewed',survivor='both',note='Separate facilities confirmed.')]);self.submit_all()
        self.assertEqual(2,len([p for p in review.read_state(self.path)['proposals'] if p['review_state']=='ready']))
        self.assertEqual(0,self.c.execute("SELECT count(*) FROM crm_accounts WHERE duplicate_of_account IS NOT NULL").fetchone()[0])

    def test_duplicate_rejection_does_not_release_children(self):
        p=self.duplicates();review.stage(self.path,[self.item(p,'rejected')]);self.submit_all()
        self.assertEqual(0,len([p for p in review.read_state(self.path)['proposals'] if p['review_state']=='ready']))

    def test_duplicate_selection_and_note_required(self):
        p=self.duplicates()
        for extra in ({'survivor':'A'},{'survivor':'Q','note':'wrong'},{'survivor':'both','note':'wrong'}):
            with self.assertRaises(ValueError):review.stage(self.path,[self.item(p,**extra)])
        self.assertEqual(0,self.c.execute('SELECT count(*) FROM decisions').fetchone()[0])

    def test_manual_fields_and_concurrent_revision(self):
        p=self.build(name='Old House')
        with self.assertRaises(ValueError):review.stage(self.path,[self.item(p,manual_values={'parent_id':'Q'})])
        review.stage(self.path,[self.item(p,manual_values={'name':'Reviewed Name'},note='Verified spelling')])
        with self.assertRaisesRegex(ValueError,'another window'):review.stage(self.path,[self.item(p,'rejected')])
        self.submit_all()
        self.assertEqual('Reviewed Name',self.c.execute("SELECT name FROM crm_accounts WHERE account_id='A'").fetchone()[0])

    def test_busy_pipeline_blocks_decisions(self):
        p=self.build(name='Old House')
        self.c.execute("INSERT INTO runs(run_id,started_at,status,run_type) VALUES ('busy','now','running','pipeline')");self.c.commit()
        with self.assertRaisesRegex(ValueError,'running'):review.stage(self.path,[self.item(p)])

    def test_proposal_version_changes_require_refresh(self):
        p=self.build(name='Old House')
        self.c.execute("UPDATE proposals SET supporting_evidence=json_set(supporting_evidence,'$.test',1)");self.c.commit()
        with self.assertRaisesRegex(ValueError,'changed'):review.stage(self.path,[self.item(p)])

    def test_creation_and_absence_operations(self):
        self.account('A',name='Old Facility',billing_street='9 Old Road')
        self.prepare();self.match()
        ps=self.proposals()
        self.assertEqual({'create_account','absent_from_website'},{p['classification'] for p in ps})
        review.stage(self.path,[self.item(p) for p in ps]);self.submit_all()
        old=self.c.execute("SELECT status,note FROM crm_accounts WHERE account_id='A'").fetchone()
        self.assertEqual('Inactive',old['status']);self.assertIn('Not found',old['note'])
        new=self.c.execute("SELECT * FROM crm_accounts WHERE account_id LIKE 'LOCAL-%'").fetchone()
        self.assertEqual('Bellhaven House',new['name']);self.assertEqual('P',new['parent_id'])
        self.assertEqual(1,new['created_by_candidate'])

    def test_creation_does_not_duplicate_newly_added_account(self):
        self.prepare();self.match();p=self.proposals()[0]
        review.stage(self.path,[self.item(p)])
        self.account('NEW')
        with self.assertRaisesRegex(ValueError,'matching account now exists'):self.submit_all()
        self.assertEqual(0,self.c.execute("SELECT count(*) FROM crm_accounts WHERE account_id LIKE 'LOCAL-%'").fetchone()[0])

    def test_manual_fields_rechecked_at_submission(self):
        p=self.build(name='Old House')
        review.stage(self.path,[self.item(p,manual_values={'care_type':'Assisted Living'})])
        self.c.execute("UPDATE crm_accounts SET care_type='Other care' WHERE account_id='A'");self.c.commit()
        with self.assertRaisesRegex(ValueError,'field changed'):self.submit_all()
        self.assertEqual('Old House',self.c.execute("SELECT name FROM crm_accounts WHERE account_id='A'").fetchone()[0])

    def test_no_change_matches_are_automatic_and_rerun_safe(self):
        p=self.build()
        before=self.c.execute('SELECT * FROM crm_accounts ORDER BY account_id').fetchall()
        row=review.read_state(self.path)['proposals'][0]
        self.assertTrue(row['decision']['automatic'])
        self.assertEqual('reviewed',row['decision']['choice'])
        self.assertEqual([],row['decision']['approved_changes'])
        self.assertTrue(row['decision']['submitted_at'])
        self.assertEqual('decided',row['review_state'])
        self.assertEqual(0,self.c.execute('SELECT count(*) FROM change_history').fetchone()[0])
        self.prepare('R2');result=self.match('R2')
        self.assertEqual(1,result['decided_proposals_skipped'])
        self.assertEqual(1,self.c.execute('SELECT count(*) FROM decisions').fetchone()[0])
        self.assertEqual(before,self.c.execute('SELECT * FROM crm_accounts ORDER BY account_id').fetchall())

    def test_duplicate_no_change_matches_wait_then_complete_automatically(self):
        self.account('A');self.account('B');self.prepare();self.match()
        self.assertEqual(0,self.c.execute('SELECT count(*) FROM decisions').fetchone()[0])
        group=self.proposals('duplicate_resolution')[0]
        review.stage(self.path,[self.item(group,survivor='A',note='Keep A.')]);self.submit_all()
        survivor=next(p for p in review.read_state(self.path)['proposals'] if p['account_id']=='A')
        loser=next(p for p in review.read_state(self.path)['proposals'] if p['account_id']=='B')
        self.assertTrue(survivor['decision']['automatic'])
        self.assertEqual('rejected',loser['decision']['choice'])
        self.assertEqual(1,self.c.execute('SELECT count(*) FROM change_history').fetchone()[0])

    def test_existing_match_backfill_does_not_override_human_or_stale_cases(self):
        from unittest.mock import patch
        import match_records
        self.account('A');self.prepare()
        with patch.object(match_records,'auto_complete_matches'):
            self.match()
        p=self.proposals()[0]
        self.c.execute("UPDATE crm_accounts SET name='Changed' WHERE account_id='A'");self.c.commit()
        self.assertEqual(0,review.complete_existing_matches(self.path))
        self.c.execute("UPDATE crm_accounts SET name='Bellhaven House' WHERE account_id='A'");self.c.commit()
        review.stage(self.path,[self.item(p,'rejected')])
        self.assertEqual(0,review.complete_existing_matches(self.path))
        self.assertEqual('rejected',self.c.execute('SELECT choice FROM decisions').fetchone()[0])

    def test_http_mode_gate_and_actual_submission(self):
        p=self.build(name='Old House')
        http=server.make_server(0,self.path)
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        self.addCleanup(http.server_close);self.addCleanup(http.shutdown)
        base=f'http://127.0.0.1:{http.server_port}'
        def call(path,data=None,token=None,origin=None):
            headers={'Content-Type':'application/json'}
            if token:headers['X-Bellhaven-Session']=token
            if origin:headers['Origin']=origin
            req=Request(base+path, data=json.dumps(data).encode() if data is not None else None,headers=headers)
            with urlopen(req) as response:return json.load(response)
        with self.assertRaises(HTTPError) as error:call('/api/state')
        self.assertEqual(401,error.exception.code)
        with self.assertRaises(HTTPError):call('/api/session',{'mode':'production'})
        with self.assertRaises(HTTPError):call('/api/session',{'mode':'test'},origin='https://other.example')
        with patch.object(http.app,'start',return_value='test-run'):
            token=call('/api/session',{'mode':'test'})['token']
        data=call('/api/decisions/stage',{'items':[self.item(p)]},token)
        d=data['proposals'][0]['decision']
        data=call('/api/decisions/submit',{'items':[{'proposal_id':p['proposal_id'],'revision':d['decided_at']}]},token)
        self.assertTrue(data['proposals'][0]['decision']['submitted_at'])
        self.assertEqual('Bellhaven House',next(a['name'] for a in data['accounts'] if a['account_id']=='A'))


if __name__=='__main__':unittest.main()

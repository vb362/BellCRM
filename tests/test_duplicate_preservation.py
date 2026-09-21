"""Duplicate contact/phone review and API failure recovery, using temporary data."""
import json
import unittest

import production_api as api
import review_service as review
import test_review_service as review_fixtures
import test_supplemental_updates as contact_fixtures
import test_production_api as api_fixtures


class DuplicatePreservationTests(unittest.TestCase):
    setUp = api_fixtures.ProductionTests.setUp
    account = review_fixtures.ReviewTests.account
    contact = contact_fixtures.SupplementalTests.contact
    prepare = review_fixtures.ReviewTests.prepare
    match = review_fixtures.ReviewTests.match
    proposals = review_fixtures.ReviewTests.proposals
    item = review_fixtures.ReviewTests.item
    submit_all = review_fixtures.ReviewTests.submit_all
    service = api_fixtures.ProductionTests.service
    execute = api_fixtures.ProductionTests.execute

    def group(self, inactive=False, same_name=False):
        self.account('A', name='Old House', phone='111-1111')
        self.account('B', name='Old House', phone='222-2222')
        self.contact('GLORIA', name='Gloria Lambert', email=None)
        self.contact('TRICIA', account_id='B', name='Gloria Lambert' if same_name else 'Tricia Lindqvist',
                     title='Admissions Director', email='tricia@example.test')
        if inactive:
            self.contact('FORMER', account_id='B', name='Former Person', is_active=0)
        self.prepare(websites=[dict(administrator='Tricia Lindqvist', phone='333-3333')])
        self.match()
        return self.proposals('duplicate_resolution')[0]

    def stage_group(self, p, survivor='A', phone='B'):
        review.stage(self.path, [self.item(p, survivor=survivor, phone_account_id=phone, note='Retain both people.')])

    def plan(self, p, survivor='A'):
        self.service()
        self.stage_group(p, survivor)
        d = self.c.execute('SELECT * FROM decisions WHERE proposal_id=?', (p['proposal_id'],)).fetchone()
        return self.svc.prepare([dict(proposal_id=p['proposal_id'], revision=d['decided_at'])])

    def test_local_preserves_details_and_inactive_contacts_and_reprepares_survivor(self):
        p = self.group(inactive=True)
        self.stage_group(p)
        self.assertEqual(3, self.c.execute('SELECT count(*) FROM crm_contacts').fetchone()[0])
        self.submit_all()
        originals = {r['contact_id']: dict(r) for r in self.c.execute("SELECT * FROM crm_contacts WHERE contact_id IN ('GLORIA','TRICIA','FORMER')")}
        self.assertEqual(1, originals['GLORIA']['is_active'])
        self.assertEqual(0, originals['TRICIA']['is_active'])
        self.assertEqual(0, originals['FORMER']['is_active'])
        copies = {r['name']: dict(r) for r in self.c.execute("SELECT * FROM crm_contacts WHERE contact_id LIKE 'LOCAL-CONTACT-%'")}
        self.assertEqual(2, len(copies))
        tricia = copies['Tricia Lindqvist']
        for key in ('name', 'title', 'email', 'phone'):
            self.assertEqual(originals['TRICIA'][key], tricia[key])
        self.assertEqual('A', tricia['account_id'])
        self.assertEqual(1, tricia['is_active'])
        self.assertEqual(0, copies['Former Person']['is_active'])
        self.assertEqual('222-2222', self.c.execute("SELECT phone FROM crm_accounts WHERE account_id='A'").fetchone()[0])
        child = next(p for p in review.read_state(self.path)['proposals'] if p['account_id'] == 'A')
        self.assertFalse(any(o['action'] == 'create_contact' for o in child['proposed_changes']))
        self.assertFalse(any('phone' in o.get('values', {}) for o in child['proposed_changes']))
        self.assertNotIn('current phone', child['explanation'])
        self.assertEqual(1, child['explanation'].count('The website also supplies current'))
        self.assertTrue(any(o.get('contact_id') == tricia['contact_id'] for o in child['proposed_changes']))
        review.stage(self.path, [self.item(child)])
        self.submit_all()
        self.assertEqual('222-2222', self.c.execute("SELECT phone FROM crm_accounts WHERE account_id='A'").fetchone()[0])

    def test_phone_requires_explicit_valid_selection(self):
        p = self.group()
        for extra in ({}, {'phone_account_id': 'OTHER'}):
            with self.assertRaisesRegex(ValueError, 'phone'):
                review.stage(self.path, [self.item(p, survivor='A', note='Keep both people', **extra)])
        self.assertEqual(0, self.c.execute('SELECT count(*) FROM decisions').fetchone()[0])

    def test_changed_contacts_or_phone_blocks_staging_and_submission(self):
        p = self.group()
        self.c.execute("UPDATE crm_contacts SET email='new@example.test' WHERE contact_id='TRICIA'")
        self.c.commit()
        with self.assertRaisesRegex(ValueError, 'contacts changed'):
            self.stage_group(p)
        self.c.execute("UPDATE crm_contacts SET email='tricia@example.test' WHERE contact_id='TRICIA'")
        self.c.commit()
        self.stage_group(p)
        self.c.execute("UPDATE crm_accounts SET phone='999' WHERE account_id='B'")
        self.c.commit()
        with self.assertRaisesRegex(ValueError, 'stale'):
            self.submit_all()
        self.assertEqual(2, self.c.execute('SELECT count(*) FROM crm_contacts').fetchone()[0])

    def test_keep_all_and_reject_do_not_copy_contacts_or_change_phone(self):
        p = self.group()
        review.stage(self.path, [self.item(p, choice='reviewed', survivor='both', note='Separate records')])
        self.submit_all()
        self.assertEqual(2, self.c.execute('SELECT count(*) FROM crm_contacts WHERE is_active=1').fetchone()[0])
        self.assertEqual(0, self.c.execute('SELECT count(*) FROM change_history').fetchone()[0])

    def test_legacy_card_can_be_reviewed_without_rescanning(self):
        p = self.group()
        evidence = p['supporting_evidence']
        for key in ('duplicate_resolution_version', 'contacts', 'contact_preconditions'):
            evidence.pop(key)
        self.c.execute('UPDATE proposals SET supporting_evidence=? WHERE proposal_id=?', (json.dumps(evidence), p['proposal_id']))
        self.c.commit()
        shown = next(p for p in review.read_state(self.path)['proposals'] if p['classification'] == 'duplicate_resolution')
        self.assertEqual(2, len(shown['supporting_evidence']['contacts']))
        self.stage_group(shown)
        self.submit_all()
        self.assertEqual(3, self.c.execute('SELECT count(*) FROM crm_contacts').fetchone()[0])

    def test_old_staged_account_only_resolution_requires_review(self):
        p = self.group()
        self.stage_group(p)
        old_ops = [dict(action='update', account_id='B', values={'status': 'Inactive', 'duplicate_of_account': 'A'}),
                   dict(action='append_note', account_id='B', text='Old decision')]
        self.c.execute('UPDATE decisions SET approved_changes=?', (json.dumps(old_ops),))
        self.c.commit()
        with self.assertRaisesRegex(ValueError, 'Review the duplicate again'):
            self.submit_all()

    def test_api_verifies_copy_before_deactivating_original_and_account(self):
        p = self.group()
        saved = self.plan(p)
        calls = saved['plan']['requests']
        self.assertEqual(('POST', '/contacts'), (calls[0]['method'], calls[0]['path']))
        self.assertEqual('/contacts/TRICIA', calls[1]['path'])
        self.assertEqual('/accounts/B', calls[-2]['path'])
        self.assertFalse(self.crm.writes)
        self.execute(saved)
        post = next(i for i, call in enumerate(self.crm.calls) if call[0] == 'POST')
        self.assertEqual(('GET', '/contacts/0031', None), self.crm.calls[post+1])
        self.assertEqual('tricia@example.test', self.crm.data['contacts']['0031']['email'])
        self.assertEqual('Inactive', self.crm.data['accounts']['B']['status'])
        before = len(self.crm.writes)
        self.execute(saved)
        self.assertEqual(before, len(self.crm.writes))

    def test_copy_verification_failure_keeps_original_active(self):
        p = self.group()
        saved = self.plan(p)
        self.crm.fail = 'wrong_readback'
        with self.assertRaisesRegex(ValueError, 'read-back differs'):
            self.execute(saved)
        self.assertEqual(1, self.crm.data['contacts']['TRICIA']['is_active'])
        self.assertEqual('Active', self.crm.data['accounts']['B']['status'])
        self.assertEqual(1, len(self.crm.writes))
        self.assertEqual(0, self.c.execute('SELECT count(*) FROM change_history').fetchone()[0])

    def test_uncertain_copy_resumes_with_verified_id_without_recreating(self):
        p = self.group()
        saved = self.plan(p)
        self.crm.fail = 'timeout_after_write'
        with self.assertRaises(api.APIError):
            self.execute(saved)
        with self.assertRaisesRegex(ValueError, 'unknown outcome'):
            self.execute(saved)
        self.assertEqual('Active', self.crm.data['accounts']['B']['status'])
        self.svc.execute(saved['plan_id'], saved['digest'], {'1': '0031'})
        self.assertEqual(1, sum(call[0] == 'POST' for call in self.crm.writes))
        self.assertEqual('complete', self.svc.state()['status'])

    def test_either_survivor_works_and_distinct_contacts_with_same_name_are_retained(self):
        p = self.group(same_name=True)
        self.contact('SECOND', account_id='A', name='Gloria Lambert', title='Billing', email='billing@example.test')
        # Rebuild evidence as an unreviewed card would be refreshed by a new run.
        self.prepare('R2', websites=[dict(phone='333-3333')]); self.match('R2')
        p = self.proposals('duplicate_resolution')[0]
        saved = self.plan(p, survivor='B')
        self.execute(saved)
        retained = [c for c in self.crm.data['contacts'].values() if c['account_id'] == 'B' and c['is_active']]
        self.assertEqual(3, len(retained))
        self.assertEqual({'Administrator', 'Admissions Director', 'Billing'}, {c['title'] for c in retained})


if __name__ == '__main__':
    unittest.main()

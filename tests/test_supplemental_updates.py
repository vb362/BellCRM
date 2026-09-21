"""Account enrichment and contact decisions, using isolated databases."""
import json
import unittest
from unittest.mock import patch

import match_records as matcher
import review_service as review
from supplemental_updates import phone_key, care_key
import test_review_service as review_fixtures


class SupplementalTests(unittest.TestCase):
    setUp = review_fixtures.ReviewTests.setUp
    account = review_fixtures.ReviewTests.account
    prepare = review_fixtures.ReviewTests.prepare
    match = review_fixtures.ReviewTests.match
    proposals = review_fixtures.ReviewTests.proposals
    item = review_fixtures.ReviewTests.item
    submit_all = review_fixtures.ReviewTests.submit_all

    def contact(self, cid='C', **overrides):
        data = dict(contact_id=cid, account_id='A', name='Former Person', title='Administrator',
                    email='former@example.test', phone='555-1111', is_active=1,
                    created_by_candidate=0, updated_at='original')
        data.update(overrides)
        self.c.execute(f'INSERT INTO crm_contacts ({",".join(data)}) VALUES ({",".join("?" for _ in data)})', tuple(data.values()))
        self.c.commit()

    def build(self, admin=None, **website):
        self.prepare(websites=[dict(administrator=admin, **website)])
        self.match()
        return self.proposals()[0]

    def test_perfect_match_with_extras_needs_review_and_updates(self):
        self.account('A')
        p=self.build(phone='555-9999',care_offerings='["Memory Support", "Assisted Living"]')
        self.assertEqual('confident_match',p['classification'])
        self.assertEqual('ready',matcher.proposal_review_state(self.c,p['proposal_id']))
        self.assertIn('phone, care offerings',p['title'])
        self.assertEqual(0,self.c.execute('SELECT count(*) FROM decisions').fetchone()[0])
        review.stage(self.path,[self.item(p)]);self.submit_all()
        a=self.c.execute("SELECT * FROM crm_accounts WHERE account_id='A'").fetchone()
        self.assertEqual('555-9999',a['phone']);self.assertEqual('Memory Support; Assisted Living',a['care_type'])
        self.assertEqual('Bellhaven House',a['name'])

    def test_formatting_sets_and_missing_values_do_not_overwrite(self):
        self.account('A',phone='+1 (212) 555-0100',care_type='Memory Support; Assisted Living')
        p=self.build(phone='212.555.0100',care_offerings='["assisted living", "Memory Support"]')
        self.assertEqual([],p['proposed_changes'])
        self.assertEqual(phone_key('+1 (212) 555-0100 ext. 23'),phone_key('212-555-0100 x23'))
        self.assertNotEqual(phone_key('2125550100 x23'),phone_key('2125550100 x24'))
        self.assertNotEqual(care_key('Skilled Nursing'),care_key(['Short-Term Rehabilitation & Nursing']))
        self.prepare('R2',websites=[dict(phone='',care_offerings='[]',administrator='')]);self.match('R2')
        self.assertTrue(all(not p['proposed_changes'] for p in self.proposals()))

    def test_matching_administrator_no_contact_edits(self):
        self.account('A');self.contact(name='Dale Amato')
        p=self.build(' dale  AMATO ')
        self.assertEqual([],p['proposed_changes'])

    def test_replacement_preserves_old_person_and_new_has_no_inherited_details(self):
        self.account('A');self.contact()
        p=self.build('New Person')
        review.stage(self.path,[self.item(p)]);self.submit_all()
        old=dict(self.c.execute("SELECT * FROM crm_contacts WHERE contact_id='C'").fetchone())
        self.assertEqual(0,old['is_active']);self.assertEqual('Former Person',old['name'])
        self.assertEqual('former@example.test',old['email']);self.assertEqual('555-1111',old['phone'])
        new=dict(self.c.execute("SELECT * FROM crm_contacts WHERE contact_id<>'C'").fetchone())
        self.assertEqual('New Person',new['name']);self.assertEqual('Administrator',new['title'])
        self.assertEqual(1,new['is_active']);self.assertIsNone(new['email']);self.assertIsNone(new['phone'])
        history=review.read_state(self.path)['proposals'][0]['history'][0]
        self.assertTrue(all('contact_id' in r for r in history['after_values']))
        self.prepare('R2',websites=[dict(administrator='New Person')]);self.match('R2')
        self.assertFalse(any(p['review_state']=='ready' for p in review.read_state(self.path)['proposals']))

    def test_existing_other_role_is_reused_and_reactivated(self):
        self.account('A');self.contact();self.contact('NEW',name='New Person',title='Admissions Director',is_active=0,email='new@example.test')
        p=self.build('New Person')
        self.assertFalse(any(o['action']=='create_contact' for o in p['proposed_changes']))
        review.stage(self.path,[self.item(p)]);self.submit_all()
        new=self.c.execute("SELECT * FROM crm_contacts WHERE contact_id='NEW'").fetchone()
        self.assertEqual(('Administrator',1,'new@example.test'),(new['title'],new['is_active'],new['email']))
        self.assertEqual(2,self.c.execute('SELECT count(*) FROM crm_contacts').fetchone()[0])

    def test_new_admin_on_account_without_contacts(self):
        self.account('A');p=self.build('New Person')
        self.assertEqual(['create_contact'],[o['action'] for o in p['proposed_changes']])
        review.stage(self.path,[self.item(p)]);self.submit_all()
        self.assertEqual(1,self.c.execute('SELECT count(*) FROM crm_contacts').fetchone()[0])

    def test_person_names_do_not_use_facility_at_of_normalization(self):
        self.account('A');self.contact(name='Pat At Hill')
        p=self.build('Pat Of Hill')
        self.assertTrue(any(o['action']=='create_contact' for o in p['proposed_changes']))

    def test_contacts_changed_or_added_after_review_block_entire_proposal(self):
        for mode in ('changed','added'):
            with self.subTest(mode=mode):
                # Separate setup per subcase below is unnecessary: roll back the change.
                if mode=='changed':
                    self.account('A');self.contact();p=self.build('New Person',phone='555-9999')
                    review.stage(self.path,[self.item(p)])
                    self.c.execute("UPDATE crm_contacts SET email='changed' WHERE contact_id='C'");self.c.commit()
                else:
                    self.c.execute("UPDATE crm_contacts SET email='former@example.test' WHERE contact_id='C'");self.c.commit()
                    self.contact('EXTRA',title='Admissions Director')
                with self.assertRaisesRegex(ValueError,'contacts changed'):self.submit_all()
                self.assertEqual('555-0100',self.c.execute("SELECT phone FROM crm_accounts WHERE account_id='A'").fetchone()[0])
                self.assertEqual(0,self.c.execute('SELECT count(*) FROM change_history').fetchone()[0])

    def test_transaction_rolls_back_account_and_new_contact_on_late_failure(self):
        self.account('A');self.contact();p=self.build('New Person',phone='555-9999')
        review.stage(self.path,[self.item(p)])
        self.c.executescript("CREATE TRIGGER fail_deactivate BEFORE UPDATE ON crm_contacts WHEN NEW.is_active=0 BEGIN SELECT RAISE(ABORT,'test failure'); END;")
        with self.assertRaisesRegex(Exception,'test failure'):self.submit_all()
        self.assertEqual(1,self.c.execute('SELECT count(*) FROM crm_contacts').fetchone()[0])
        self.assertEqual('555-0100',self.c.execute("SELECT phone FROM crm_accounts WHERE account_id='A'").fetchone()[0])
        self.assertIsNone(self.c.execute('SELECT submitted_at FROM decisions').fetchone()[0])

    def test_chow_preserves_old_contacts_and_places_admin_on_new_account(self):
        self.account('A',parent_id='Q',lifetime_revenue=10,outstanding_ar=2);self.contact()
        old=dict(self.c.execute("SELECT * FROM crm_contacts WHERE contact_id='C'").fetchone())
        p=self.build('New Person',phone='555-9999')
        review.stage(self.path,[self.item(p)]);self.submit_all()
        self.assertEqual(old,dict(self.c.execute("SELECT * FROM crm_contacts WHERE contact_id='C'").fetchone()))
        new=self.c.execute("SELECT * FROM crm_accounts WHERE account_id LIKE 'LOCAL-%'").fetchone()
        admin=self.c.execute("SELECT * FROM crm_contacts WHERE contact_id<>'C'").fetchone()
        self.assertEqual(new['account_id'],admin['account_id']);self.assertEqual('555-9999',new['phone'])
        self.assertEqual('555-0100',self.c.execute("SELECT phone FROM crm_accounts WHERE account_id='A'").fetchone()[0])

    def test_create_account_and_contact_are_linked(self):
        p=self.build('New Person')
        review.stage(self.path,[self.item(p)]);self.submit_all()
        self.assertEqual(self.c.execute('SELECT account_id FROM crm_contacts').fetchone()[0],self.c.execute("SELECT account_id FROM crm_accounts WHERE account_id LIKE 'LOCAL-%'").fetchone()[0])

    def test_duplicate_loser_extras_dismissed_survivor_requires_review(self):
        self.account('A');self.account('B');self.prepare(websites=[dict(administrator='New Person',phone='555-9999')]);self.match()
        group=self.proposals('duplicate_resolution')[0]
        corrections=self.proposals('confident_match')
        for p in corrections:
            with self.assertRaisesRegex(ValueError,'duplicate'):review.stage(self.path,[self.item(p)])
        review.stage(self.path,[self.item(group,survivor='A',note='Keep A')]);self.submit_all()
        state=review.read_state(self.path)['proposals']
        winner=next(p for p in state if p['account_id']=='A');loser=next(p for p in state if p['account_id']=='B')
        self.assertIsNone(winner['decision']);self.assertEqual('rejected',loser['decision']['choice'])
        review.stage(self.path,[self.item(winner)]);self.submit_all()
        self.assertEqual(['A'],[r[0] for r in self.c.execute('SELECT account_id FROM crm_contacts')])
        self.assertEqual('555-0100',self.c.execute("SELECT phone FROM crm_accounts WHERE account_id='B'").fetchone()[0])

    def test_ambiguous_administrators_require_explicit_valid_selection(self):
        self.account('A');self.contact();self.contact('C2',name='Another Person')
        p=self.build('New Person')
        with self.assertRaisesRegex(ValueError,'Choose the administrator'):review.stage(self.path,[self.item(p)])
        with self.assertRaisesRegex(ValueError,'Invalid former'):review.stage(self.path,[self.item(p,administrator_resolution=dict(contact_id='new',deactivate_ids=['outside'],confirmed=True))])
        review.stage(self.path,[self.item(p,administrator_resolution=dict(contact_id='new',deactivate_ids=['C'],confirmed=True))]);self.submit_all()
        self.assertEqual(0,self.c.execute("SELECT is_active FROM crm_contacts WHERE contact_id='C'").fetchone()[0])
        self.assertEqual(1,self.c.execute("SELECT is_active FROM crm_contacts WHERE contact_id='C2'").fetchone()[0])

    def test_rejected_name_does_not_return_with_new_phone_or_admin(self):
        self.account('A',name='Old House');p=self.build()
        review.stage(self.path,[self.item(p,'rejected')]);self.submit_all()
        self.prepare('R2',websites=[dict(phone='555-9999',administrator='New Person')]);self.match('R2')
        ready=[p for p in review.read_state(self.path)['proposals'] if p['review_state']=='ready']
        self.assertEqual(1,len(ready));p=ready[0]
        self.assertNotIn('facility name',p['title'])
        self.assertNotIn('name',next(o['values'] for o in p['proposed_changes'] if o['action']=='update'))
        review.stage(self.path,[self.item(p,'rejected')]);self.submit_all()
        self.prepare('R3',websites=[dict(phone='555-9999',administrator='New Person')]);self.match('R3')
        self.assertFalse(any(p['review_state']=='ready' for p in review.read_state(self.path)['proposals']))

    def test_pending_old_card_superseded_staged_card_preserved(self):
        self.account('A',name='Old House');p=self.build()
        self.prepare('R2',websites=[dict(phone='555-9999')]);self.match('R2')
        self.assertEqual('superseded',matcher.proposal_review_state(self.c,p['proposal_id']))
        ready=next(p for p in review.read_state(self.path)['proposals'] if p['review_state']=='ready')
        review.stage(self.path,[self.item(ready)])
        self.prepare('R3',websites=[dict(phone='555-8888',administrator='New Person')]);self.match('R3')
        self.assertEqual(1,len([p for p in review.read_state(self.path)['proposals'] if p['review_state']=='ready']))
        self.submit_all()
        self.assertEqual('555-9999',self.c.execute("SELECT phone FROM crm_accounts WHERE account_id='A'").fetchone()[0])

    def test_old_automatic_match_does_not_hide_new_enrichment(self):
        self.account('A');p=self.build()
        self.assertEqual('decided',matcher.proposal_review_state(self.c,p['proposal_id']))
        self.prepare('R2',websites=[dict(administrator='New Person')]);self.match('R2')
        ready=[p for p in review.read_state(self.path)['proposals'] if p['review_state']=='ready']
        self.assertEqual(1,len(ready));self.assertEqual('create_contact',ready[0]['proposed_changes'][0]['action'])

    def test_reverted_website_reuses_pending_card_without_supersession_cycle(self):
        self.account('A')
        old=self.build(phone='555-9999')
        self.prepare('R2',websites=[dict(phone='555-8888')]);self.match('R2')
        self.prepare('R3',websites=[dict(phone='555-9999')]);self.match('R3')
        ready=[p for p in review.read_state(self.path)['proposals'] if p['review_state']=='ready']
        self.assertEqual([old['proposal_id']],[p['proposal_id'] for p in ready])

    def test_same_admin_normalization_is_supplied_to_table(self):
        self.account('A');self.contact(name='Dale Amato')
        self.build(' dale  AMATO ',phone='555-9999')
        p=review.read_state(self.path)['proposals'][0]
        self.assertEqual(p['comparison_values']['website']['administrator'],p['comparison_values']['crm']['administrator'])

    def test_multiple_same_named_contacts_can_reuse_selected_person(self):
        self.account('A');self.contact(name='New Person',title='Admissions Director')
        self.contact('C2',name='New Person',title='Executive Director')
        p=self.build('New Person')
        review.stage(self.path,[self.item(p,administrator_resolution=dict(contact_id='C2',deactivate_ids=[],confirmed=True))]);self.submit_all()
        self.assertEqual('Administrator',self.c.execute("SELECT title FROM crm_contacts WHERE contact_id='C2'").fetchone()[0])
        self.assertEqual('Admissions Director',self.c.execute("SELECT title FROM crm_contacts WHERE contact_id='C'").fetchone()[0])
        self.assertEqual(2,self.c.execute('SELECT count(*) FROM crm_contacts').fetchone()[0])


    def test_inactive_same_named_contact_does_not_repeat_resolved_admin_question(self):
        self.account('A');self.contact(name='New Person')
        self.contact('C2',name='New Person',is_active=0)
        p=self.build('New Person')
        self.assertEqual([],p['proposed_changes'])


if __name__=='__main__':unittest.main()

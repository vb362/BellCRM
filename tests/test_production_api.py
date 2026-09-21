"""Production delivery contracts. All CRM traffic is simulated; no real tokens."""
from contextlib import closing
from copy import deepcopy
import json
import sqlite3
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import production_api as api
import review_service as review
import server
from normalize_data import open_database
import test_review_service as fixtures
import test_supplemental_updates as contacts


class FakeCRM:
    def __init__(self, c):
        self.data = {e: {r[api.IDS[e]]: dict(r) for r in c.execute(f'SELECT * FROM crm_{e}')} for e in api.IDS}
        self.calls = []
        self.fail = None
        self.read_failure = False
        self.counter = 0

    def list(self, entity):
        self.calls.append(('GET', '/' + entity, None))
        return deepcopy(list(self.data[entity].values()))

    def request(self, method, path, body=None):
        self.calls.append((method, path, deepcopy(body)))
        parts = path.strip('/').split('/')
        entity = parts[0]
        if method == 'GET':
            if self.read_failure:
                self.read_failure = False
                raise api.APIError('Read timed out')
            return deepcopy(self.data[entity][parts[1]])
        failure = self.fail
        self.fail = None
        if failure == 'reject':
            raise api.APIError('CRM returned HTTP 422.', definite=True)
        if method == 'POST':
            self.counter += 1
            rid = ('001' if entity == 'accounts' else '003') + str(self.counter)
            row = {k: '' for k in api.FIELDS[entity]}
            row.update({api.IDS[entity]: rid, 'created_by_candidate': True})
            if entity == 'accounts':
                row.update(status='Active', lifetime_revenue=0, outstanding_ar=0)
            row.update(body)
            self.data[entity][rid] = row
        else:
            rid = parts[1]
            self.data[entity][rid].update(body)
            if entity == 'accounts' and 'parent_id' in body:
                self.data[entity][rid]['parent_name'] = self.data[entity][body['parent_id']]['name']
        self.data[entity][rid]['updated_at'] = 'server timestamp'
        if failure == 'timeout_after_write':
            raise api.APIError('Connection interrupted')
        if failure == 'wrong_readback':
            self.data[entity][rid]['name'] = 'Unexpected value'
        if failure == 'read_failure':
            self.read_failure = True
        return {api.IDS[entity]: rid, 'message': 'created' if method == 'POST' else 'updated'}

    @property
    def writes(self):
        return [call for call in self.calls if call[0] != 'GET']


class ProductionTests(unittest.TestCase):
    account = fixtures.ReviewTests.account
    prepare = fixtures.ReviewTests.prepare
    match = fixtures.ReviewTests.match
    proposals = fixtures.ReviewTests.proposals
    item = fixtures.ReviewTests.item
    build = fixtures.ReviewTests.build
    duplicates = fixtures.ReviewTests.duplicates
    contact = contacts.SupplementalTests.contact

    def setUp(self):
        fixtures.ReviewTests.setUp(self)
        self.c.close()
        old = self.path
        self.path = self.root / 'production.sqlite'
        old.rename(self.path)
        self.c = sqlite3.connect(self.path)
        self.c.row_factory = sqlite3.Row
        self.c.execute('PRAGMA foreign_keys=ON')
        self.addCleanup(self.c.close)

    def service(self):
        self.crm = FakeCRM(self.c)
        self.svc = api.ProductionService(self.path, self.crm)
        return self.svc

    def preview(self, ps=None):
        self.service()
        ps = ps or [p for p in self.proposals() if p['proposed_changes']]
        review.stage(self.path, [self.item(p) for p in ps])
        self.items = [dict(proposal_id=d['proposal_id'], revision=d['decided_at'])
                      for d in self.c.execute('SELECT * FROM decisions WHERE submitted_at IS NULL')]
        return self.svc.prepare(self.items)

    def execute(self, p):
        return self.svc.execute(p['plan_id'], p['digest'])

    def test_preview_is_read_only_then_exact_calls_verified_and_retry_safe(self):
        p = self.build(name='Old House')
        saved = self.preview([p])
        self.assertFalse(self.crm.writes)
        self.assertEqual('Old House', self.c.execute("SELECT name FROM crm_accounts WHERE account_id='A'").fetchone()[0])
        calls = saved['plan']['requests']
        self.assertEqual('PATCH', calls[0]['method'])
        result = self.execute(saved)
        self.assertEqual('complete', result['status'])
        self.assertEqual([(r['method'], r['path'], r['body']) for r in calls], self.crm.writes)
        self.assertEqual('server timestamp', self.c.execute("SELECT updated_at FROM crm_accounts WHERE account_id='A'").fetchone()[0])
        self.assertEqual(1, self.c.execute('SELECT count(*) FROM change_history').fetchone()[0])
        before = len(self.crm.writes); self.execute(saved)
        self.assertEqual(before, len(self.crm.writes))

    def test_chow_and_contact_resolve_actual_ids_and_preserve_old_business_fields(self):
        self.account('A', parent_id='Q', parent_name='Other (Parent Account)', lifetime_revenue=10, outstanding_ar=2)
        self.contact()
        self.prepare(websites=[dict(administrator='New Person', phone='555-9999')]); self.match()
        saved = self.preview()
        self.assertEqual('POST', saved['plan']['requests'][0]['method'])
        self.assertTrue(any(isinstance(r['body'].get('chow_current_account'), dict) for r in saved['plan']['requests']))
        old = deepcopy(self.crm.data['accounts']['A'])
        self.execute(saved)
        new = self.crm.data['accounts']['A']['chow_current_account']
        self.assertFalse(new.startswith('LOCAL'))
        self.assertEqual(old, dict(self.crm.data['accounts']['A'], chow_current_account=old['chow_current_account'], updated_at=old['updated_at']))
        created = next(c for c in self.crm.data['contacts'].values() if c['contact_id'] != 'C')
        self.assertEqual(new, created['account_id']); self.assertIs(created['is_active'], True)
        self.assertEqual('A', self.crm.data['contacts']['C']['account_id'])
        self.assertEqual(1, self.c.execute('SELECT count(*) FROM change_history').fetchone()[0])

    def test_duplicate_note_appends_and_releases_survivor(self):
        p = self.duplicates()
        self.c.execute("UPDATE crm_accounts SET note='Earlier note' WHERE account_id='B'"); self.c.commit()
        self.service()
        review.stage(self.path, [self.item(p, survivor='A', note='Keep survivor')])
        d = self.c.execute('SELECT * FROM decisions').fetchone()
        saved = self.svc.prepare([dict(proposal_id=p['proposal_id'], revision=d['decided_at'])])
        self.assertEqual('Earlier note\nKeep survivor', saved['plan']['requests'][1]['body']['note'])
        self.execute(saved)
        self.assertEqual('Inactive', self.crm.data['accounts']['B']['status'])
        self.assertEqual('A', self.crm.data['accounts']['B']['duplicate_of_account'])
        self.assertEqual('Earlier note\nKeep survivor', self.crm.data['accounts']['B']['note'])
        self.assertEqual(['A'], [p['account_id'] for p in review.read_state(self.path)['proposals'] if p['review_state'] == 'ready'])

    def test_contact_updates_send_booleans_and_preserve_details(self):
        self.account('A'); self.contact(); self.contact('NEW', name='New Person', title='Other', is_active=0)
        self.prepare(websites=[dict(administrator='New Person')]); self.match()
        saved = self.preview(); self.execute(saved)
        self.assertEqual(2, len(self.crm.writes))
        self.assertTrue(all(type(r[2]['is_active']) is bool for r in self.crm.writes))
        self.assertEqual('former@example.test', self.crm.data['contacts']['C']['email'])
        self.assertFalse(self.crm.data['contacts']['C']['is_active'])
        self.assertTrue(self.crm.data['contacts']['NEW']['is_active'])

    def test_changed_decision_or_remote_data_blocks_confirmation(self):
        p = self.build(name='Old House'); saved = self.preview([p])
        self.crm.data['accounts']['A']['phone'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'CRM changed'): self.execute(saved)
        self.assertFalse(self.crm.writes)
        self.crm.data['accounts']['A']['phone'] = '555-0100'
        d = self.c.execute('SELECT * FROM decisions').fetchone()
        review.stage(self.path, [dict(self.item(p, choice='rejected'), revision=d['decided_at'])])
        self.assertEqual('invalidated', self.svc.state()['status'])
        with self.assertRaisesRegex(ValueError, 'Decisions changed'): self.execute(saved)
        self.assertFalse(self.crm.writes)

    def test_unknown_post_never_repeats_and_can_adopt_verified_new_id(self):
        self.prepare(); self.match(); saved = self.preview()
        self.crm.fail = 'timeout_after_write'
        with self.assertRaises(api.APIError): self.execute(saved)
        self.assertEqual('paused', self.svc.state()['status'])
        with self.assertRaisesRegex(ValueError, 'unknown outcome'): self.execute(saved)
        self.assertEqual(1, len(self.crm.writes))
        with self.assertRaisesRegex(ValueError, 'Finish the saved'): review.unstage(self.path, self.items[0]['proposal_id'], self.items[0]['revision'])
        with self.assertRaises(ValueError): self.svc.cancel(saved['plan_id'], saved['digest'])
        self.svc = api.ProductionService(self.path, self.crm)  # restart/reopen
        result = self.svc.execute(saved['plan_id'], saved['digest'], {'1': '0011'})
        self.assertEqual('complete', result['status']); self.assertEqual(1, len(self.crm.writes))
        self.assertEqual('0011', self.c.execute("SELECT account_id FROM crm_accounts WHERE account_id NOT IN ('P','Q')").fetchone()[0])

    def test_readback_failure_resumes_without_resending_post(self):
        self.prepare(); self.match(); saved = self.preview()
        self.crm.fail = 'read_failure'
        with self.assertRaises(api.APIError): self.execute(saved)
        self.assertEqual('written', self.svc.state()['progress'][0]['status'])
        self.execute(saved)
        self.assertEqual(1, len(self.crm.writes))

    def test_ambiguous_patch_reads_result_without_replaying(self):
        p = self.build(name='Old House'); saved = self.preview([p])
        self.crm.fail = 'timeout_after_write'
        with self.assertRaises(api.APIError): self.execute(saved)
        self.execute(saved)
        self.assertEqual(1, len(self.crm.writes))

    def test_mismatch_pauses_without_success_history(self):
        p = self.build(name='Old House'); saved = self.preview([p])
        self.crm.fail = 'wrong_readback'
        with self.assertRaisesRegex(ValueError, 'read-back differs'): self.execute(saved)
        self.assertEqual(0, self.c.execute('SELECT count(*) FROM change_history').fetchone()[0])
        self.assertIsNone(self.c.execute('SELECT submitted_at FROM decisions').fetchone()[0])
        with self.assertRaises(ValueError): self.execute(saved)
        self.assertEqual(1, len(self.crm.writes))

    def test_definite_rejection_can_cancel_or_explicitly_retry(self):
        p = self.build(name='Old House'); saved = self.preview([p]); self.crm.fail = 'reject'
        with self.assertRaises(api.APIError): self.execute(saved)
        self.assertEqual('failed', self.svc.state()['progress'][0]['status'])
        self.svc.cancel(saved['plan_id'], saved['digest'])
        self.assertEqual('invalidated', self.svc.state()['status'])
        with self.assertRaises(ValueError): self.execute(saved)
        self.assertEqual('Old House', self.crm.data['accounts']['A']['name'])

    def test_rejected_decision_requires_no_write(self):
        p = self.build(name='Old House'); self.service()
        review.stage(self.path, [self.item(p, choice='rejected')])
        d = self.c.execute('SELECT * FROM decisions').fetchone()
        saved = self.svc.prepare([dict(proposal_id=p['proposal_id'], revision=d['decided_at'])])
        self.assertFalse(saved['plan']['requests']); self.execute(saved)
        self.assertFalse(self.crm.writes)
        self.assertIsNotNone(self.c.execute('SELECT submitted_at FROM decisions').fetchone()[0])

    def test_immutable_plan_and_fingerprint(self):
        p = self.build(name='Old House'); saved = self.preview([p])
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.execute("UPDATE production_plans SET plan_json='{}'")
        self.c.rollback()
        with self.assertRaises(ValueError): self.svc.execute(saved['plan_id'], 'wrong')
        self.assertFalse(self.crm.writes)

    def test_exclusive_guard_blocks_second_executor(self):
        p = self.build(name='Old House'); saved = self.preview([p])
        with self.svc.exclusive():
            with self.assertRaisesRegex(ValueError, 'already running'): self.execute(saved)
        self.assertFalse(self.crm.writes)

    def test_production_rejects_demo_database(self):
        with self.assertRaises(ValueError): api.ProductionService(self.root / 'demo.sqlite', FakeCRM(self.c))

    def test_partial_execution_restart_does_not_repeat_prior_calls(self):
        self.account('A'); self.contact()
        self.prepare(websites=[dict(administrator='New Person', phone='555-9999')]); self.match()
        saved = self.preview()
        original = self.crm.request
        count = 0
        def fail_second(method, path, body=None):
            nonlocal count
            if method != 'GET':
                count += 1
                if count == 2: self.crm.fail = 'reject'
            return original(method, path, body)
        with patch.object(self.crm, 'request', side_effect=fail_second):
            with self.assertRaises(api.APIError): self.execute(saved)
        self.assertEqual('verified', self.svc.state()['progress'][0]['status'])
        self.svc = api.ProductionService(self.path, self.crm)
        self.execute(saved)
        self.assertEqual(1, sum(method == 'PATCH' and path == '/accounts/A' for method, path, body in self.crm.writes))
        self.assertEqual('complete', self.svc.state()['status'])

    def test_parent_correction_omits_derived_name_and_verifies_it(self):
        p = self.build(parent_id='Q', parent_name='Other (Parent Account)')
        saved = self.preview([p])
        body = saved['plan']['requests'][0]['body']
        self.assertEqual('P', body['parent_id'])
        self.assertNotIn('parent_name', body)
        self.execute(saved)
        self.assertEqual('Bellhaven Senior Living (Parent Account)', self.crm.data['accounts']['A']['parent_name'])

    def test_refresh_imports_all_parents_accounts_and_contacts(self):
        self.account('A', parent_id='Q'); self.contact()
        self.service()
        self.c.execute('DELETE FROM crm_accounts'); self.c.execute('DELETE FROM crm_contacts'); self.c.commit()
        self.svc.refresh()
        self.assertEqual({'P', 'Q', 'A'}, {r[0] for r in self.c.execute('SELECT account_id FROM crm_accounts')})
        self.assertEqual(1, self.c.execute('SELECT count(*) FROM crm_contacts').fetchone()[0])
        self.assertFalse(self.crm.writes)

    def test_started_submission_blocks_pipeline_and_staging(self):
        from run_pipeline import start_pipeline
        p = self.build(name='Old House'); saved = self.preview([p]); self.crm.fail = 'reject'
        with self.assertRaises(api.APIError): self.execute(saved)
        with self.assertRaisesRegex(ValueError, 'Finish the saved'): start_pipeline(self.path)
        with self.assertRaisesRegex(ValueError, 'Finish the saved'):
            review.stage(self.path, [dict(self.item(p), revision=self.items[0]['revision'])])

    def test_http_production_session_requires_preview_confirmation_and_isolates_test(self):
        p = self.build(name='Old House'); saved = self.preview([p])
        demo = self.root / 'demo.sqlite'
        with sqlite3.connect(demo) as target: self.c.backup(target)
        http = server.make_server(0, demo, self.path, self.crm)
        thread = threading.Thread(target=http.serve_forever, daemon=True); thread.start()
        self.addCleanup(http.server_close); self.addCleanup(http.shutdown)
        base = f'http://127.0.0.1:{http.server_port}'
        def call(path, data=None, token=None):
            req = Request(base + path, data=None if data is None else json.dumps(data).encode(),
                          headers={'Content-Type': 'application/json', **({'X-Bellhaven-Session': token} if token else {})})
            with urlopen(req) as response: return json.load(response)
        with patch.object(server.Application, 'start', return_value='mock-run'):
            prod = call('/api/session', {'mode': 'production'})['token']
            test = call('/api/session', {'mode': 'test'})['token']
        self.assertEqual('production', call('/api/state', token=prod)['mode'])
        self.assertEqual('test', call('/api/state', token=test)['mode'])
        with self.assertRaises(HTTPError): call('/api/production/preview', {'items': self.items}, test)
        with self.assertRaises(HTTPError): call('/api/decisions/submit', {'items': self.items}, prod)
        state = call('/api/production/preview', {'items': self.items}, prod)
        plan = state['production']
        result = call('/api/decisions/submit', {'plan_id': plan['plan_id'], 'digest': plan['digest'], 'confirm': True}, prod)
        self.assertEqual('complete', result['production']['status'])
        self.assertEqual('Old House', next(r['name'] for r in call('/api/state', token=test)['accounts'] if r['account_id'] == 'A'))


class ClientTests(unittest.TestCase):
    def test_full_pagination_and_incomplete_pages_rejected(self):
        client = api.Client('fake')
        row = {k: None for k in api.CRM_FIELDS}; row['account_id'] = 'A'
        other = dict(row, account_id='B')
        with patch.object(client, 'request', side_effect=[dict(total=2, data=[row]), dict(total=2, data=[other])]) as call:
            self.assertEqual(2, len(client.list('accounts')))
            self.assertIn('page=2', call.call_args[0][1])
        with patch.object(client, 'request', return_value=dict(total=2, data=[])):
            with self.assertRaises(ValueError): client.list('accounts')

    def test_client_does_not_redirect_retry_or_leak_token(self):
        client = api.Client('secret-for-test')
        with patch.object(client.session, 'request', side_effect=api.requests.Timeout('secret-for-test')) as call:
            with self.assertRaises(api.APIError) as error: client.request('POST', '/accounts', {'name': 'Test'})
            self.assertNotIn('secret-for-test', str(error.exception))
            self.assertEqual(1, call.call_count)
            self.assertFalse(call.call_args.kwargs['allow_redirects'])
        with self.assertRaises(ValueError): client.request('GET', 'https://elsewhere.example')


if __name__ == '__main__': unittest.main()

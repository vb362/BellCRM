"""Production-only, preview-first CRM delivery. No network work at import time.

Tokens stay on the server (BELLHAVEN_API_TOKEN). Writes require a saved plan's
ID and digest. Every write is journalled before sending, with no HTTP retries.
"""
from contextlib import closing, contextmanager
from copy import deepcopy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from uuid import uuid4

import requests

from normalize_data import DATABASES, CRM_FIELDS, open_database
from match_records import assert_proposal_applicable, proposal_review_state, auto_complete_matches
import review_service as review

BASE_URL = 'https://analyst-assessment-production.up.railway.app/api/v1'
CONTACT_FIELDS = ['contact_id', 'account_id', 'name', 'title', 'email', 'phone', 'is_active', 'created_by_candidate', 'updated_at']
FIELDS = {'accounts': list(CRM_FIELDS), 'contacts': CONTACT_FIELDS}
IDS = {'accounts': 'account_id', 'contacts': 'contact_id'}
ACTIVE = ('running', 'paused')


def digest(value):
    return hashlib.sha256(review.dumps(value).encode()).hexdigest()


def business(row):
    return {k: v for k, v in row.items() if k != 'updated_at'}


def valid_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise ValueError('Unexpected CRM record ID')
    return value


class APIError(ValueError):
    def __init__(self, message, definite=False):
        super().__init__(message)
        self.definite = definite


class Client:
    def __init__(self, token=None):
        self.token = token or os.environ.get('BELLHAVEN_API_TOKEN', '').strip()
        if not self.token:
            raise ValueError('Set BELLHAVEN_API_TOKEN on the server before choosing Production mode.')
        self.session = requests.Session()
        # requests' default adapter does not retry. Redirects are disabled below.

    def request(self, method, path, body=None):
        if not re.fullmatch(r'/(accounts|contacts)(/[A-Za-z0-9_-]+)?(\?page=\d+&page_size=\d+)?', path):
            raise ValueError('Unsupported CRM API path')
        try:
            response = self.session.request(method, BASE_URL + path, json=body,
                headers={'Authorization': 'Bearer ' + self.token, 'Accept': 'application/json'},
                timeout=(10, 30), allow_redirects=False)
        except requests.RequestException:
            raise APIError('CRM request interrupted; its outcome must be checked before continuing.') from None
        if not 200 <= response.status_code < 300:
            # Never echo request headers or arbitrary remote content into logs/UI.
            raise APIError(f'CRM returned HTTP {response.status_code}.',
                           definite=response.status_code in (400, 401, 403, 404, 405, 422, 429))
        try:
            return response.json()
        except ValueError:
            raise APIError('CRM returned an unreadable success response; verify the outcome.') from None

    def list(self, entity):
        records, seen, expected_total = [], set(), None
        for page in range(1, 10001):
            result = self.request('GET', f'/{entity}?page={page}&page_size=100')
            if not isinstance(result, dict) or not isinstance(result.get('data'), list):
                raise ValueError('Unexpected CRM list response')
            total = result.get('total')
            if type(total) is not int or total < 0 or (expected_total is not None and total != expected_total):
                raise ValueError('CRM changed during pagination. Refresh again.')
            expected_total = total
            for row in result['data']:
                validate_record(entity, row)
                key = row[IDS[entity]]
                if key in seen:
                    raise ValueError('CRM returned duplicate records during pagination')
                seen.add(key); records.append(row)
            if len(records) == total:
                return records
            if not result['data'] or len(records) > total:
                break
        raise ValueError('Incomplete CRM pagination; no local records were replaced')


def validate_record(entity, row):
    if not isinstance(row, dict) or not set(FIELDS[entity]).issubset(row):
        raise ValueError(f'CRM {entity} response is missing expected fields')
    valid_id(row[IDS[entity]])
    for key in ('created_by_candidate', 'is_active'):
        if key in row and row[key] not in (None, True, False, 0, 1):
            raise ValueError('Unexpected CRM boolean value')


def upsert(c, entity, row):
    validate_record(entity, row)
    fields = FIELDS[entity]
    key = IDS[entity]
    c.execute(f'''INSERT INTO crm_{entity} ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})
        ON CONFLICT({key}) DO UPDATE SET {','.join(f'{f}=excluded.{f}' for f in fields if f != key)}''',
        tuple(row[f] for f in fields))


def snapshot(c):
    return {e: [dict(r) for r in c.execute(f'SELECT * FROM crm_{e} ORDER BY {IDS[e]}')] for e in IDS}


def snapshot_digest(data):
    # SQLite's bool representation and JSON bools must hash identically.
    return digest({e: [{k: int(v) if isinstance(v, bool) else v for k, v in row.items()}
                       for row in sorted(data[e], key=lambda r: r[IDS[e]])] for e in IDS})


class ProductionService:
    def __init__(self, database, client=None):
        self.database = Path(database).resolve()
        demo = DATABASES['demo']
        if self.database.name in ('demo.sqlite', 'start.sqlite') or (demo.exists() and self.database.samefile(demo)):
            raise ValueError('Production API execution requires a separate production database')
        self.client = client if client is not None else Client()
        from migrate_databases import migrate_database
        migrate_database(self.database, 'production', self.database.parent / 'backups')

    @contextmanager
    def exclusive(self):
        # Also protects against a second server process and permits crash recovery.
        with self.database.with_suffix('.submission.lock').open('a') as file:
            try:
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('A production operation is already running. Wait for its result.') from None
            try:
                yield
            finally:
                fcntl.flock(file, fcntl.LOCK_UN)

    def load_remote(self):
        result = {entity: self.client.list(entity) for entity in IDS}
        for entity in IDS:
            for row in result[entity]:
                validate_record(entity, row)
        return result

    def sync(self, c, remote):
        for entity in IDS:
            ids = {r[IDS[entity]] for r in remote[entity]}
            existing = {r[0] for r in c.execute(f'SELECT {IDS[entity]} FROM crm_{entity}')}
            # Account deletion would break references in historical proposals.
            if entity == 'accounts' and existing - ids:
                raise ValueError('CRM accounts were deleted remotely; reconcile the local history before continuing.')
            for row in remote[entity]:
                upsert(c, entity, row)
            if entity == 'contacts':
                for cid in existing - ids:
                    c.execute('DELETE FROM crm_contacts WHERE contact_id=?', (cid,))

    def refresh(self):
        with self.exclusive():
            with closing(open_database(self.database)) as c:
                review.require_idle(c)
            remote = self.load_remote()
            with closing(open_database(self.database)) as c, c:
                c.execute('BEGIN IMMEDIATE'); review.require_idle(c)
                self.sync(c, remote)

    def state(self):
        with closing(open_database(self.database)) as c:
            row = c.execute("SELECT * FROM production_plans ORDER BY CASE WHEN status IN ('running','paused') THEN 0 ELSE 1 END,created_at DESC LIMIT 1").fetchone()
            if not row:
                return None
            result = dict(row)
            result['plan'] = json.loads(result.pop('plan_json'))
            result['progress'] = [dict(r) for r in c.execute('SELECT * FROM production_requests WHERE plan_id=? ORDER BY ordinal', (row['plan_id'],))]
            # Unsubmitted changes invalidate the visible preview even before confirm.
            if result['status'] == 'preview':
                try:
                    self.check_decisions(c, result['plan'])
                except ValueError:
                    result['status'] = 'invalidated'
            return result

    def check_decisions(self, c, plan):
        for saved in plan['decisions']:
            row = c.execute('SELECT * FROM decisions WHERE decision_id=?', (saved['decision_id'],)).fetchone()
            proposal = c.execute('SELECT * FROM proposals WHERE proposal_id=?', (saved['proposal_id'],)).fetchone()
            if not row or dict(row) != saved or not proposal or review.version(review.unpack(proposal)) != plan['versions'][saved['proposal_id']]:
                raise ValueError('Decisions changed after this preview. Prepare and review the calls again.')

    def prepare(self, items):
        if not isinstance(items, list) or not items or len(items) > 500 or len({i.get('proposal_id') for i in items}) != len(items):
            raise ValueError('Select distinct staged decisions to preview')
        with self.exclusive():
            with closing(open_database(self.database)) as c:
                review.require_idle(c)
            remote = self.load_remote()
            with closing(open_database(self.database)) as c, c:
                c.execute('BEGIN IMMEDIATE'); review.require_idle(c)
                self.sync(c, remote)
                plan = dict(decisions=[], versions={}, requests=[], baseline=snapshot_digest(remote))
                for item in items:
                    d = review.require_revision(c, item.get('proposal_id'), item.get('revision'))
                    if not d or d['submitted_at'] or proposal_review_state(c, d['proposal_id']) != 'ready':
                        raise ValueError('Only ready, staged decisions may be previewed')
                    p = review.unpack(c.execute('SELECT * FROM proposals WHERE proposal_id=?', (d['proposal_id'],)).fetchone())
                    plan['decisions'].append(dict(d)); plan['versions'][d['proposal_id']] = review.version(p)
                # Simulate the whole batch to catch conflicts before any remote writes.
                c.execute('SAVEPOINT simulation')
                try:
                    for d in plan['decisions']:
                        p = review.unpack(c.execute('SELECT * FROM proposals WHERE proposal_id=?', (d['proposal_id'],)).fetchone())
                        ops = json.loads(d['approved_changes'])
                        if d['choice'] != 'rejected':
                            assert_proposal_applicable(c, d['proposal_id'])
                        if ops:
                            review.check_saved_operations(c, p, ops)
                            self.compile(c, d, ops, plan['requests'])
                            review.apply_operations(c, ops)
                finally:
                    c.execute('ROLLBACK TO simulation'); c.execute('RELEASE simulation')
                pid = str(uuid4())
                c.execute("UPDATE production_plans SET status='invalidated' WHERE status='preview'")
                c.execute("INSERT INTO production_plans(plan_id,created_at,plan_json,digest,status) VALUES (?,?,?,?,'preview')",
                          (pid, review.now(), review.dumps(plan), digest(plan)))
                for req in plan['requests']:
                    c.execute('INSERT INTO production_requests(plan_id,ordinal,updated_at) VALUES (?,?,?)', (pid, req['ordinal'], review.now()))
            return self.state()

    def compile(self, c, decision, ops, output):
        refs, working = {}, {}
        for op in ops:
            action = op['action']
            if action not in ('create', 'update', 'append_note', 'create_contact', 'update_contact'):
                raise ValueError('This operation has no verified API mapping: ' + action)
            entity = 'contacts' if action.endswith('_contact') else 'accounts'
            create = action in ('create', 'create_contact')
            key = IDS[entity]
            rid = None if create else valid_id(op[key])
            before = None
            if not create:
                if (entity, rid) not in working:
                    row = c.execute(f'SELECT * FROM crm_{entity} WHERE {key}=?', (rid,)).fetchone()
                    if not row:
                        raise ValueError('Target record no longer exists')
                    working[entity, rid] = dict(row)
                before = deepcopy(working[entity, rid])
            if action == 'append_note':
                body = {'note': '\n'.join(filter(None, [before['note'], op['text']]))}
            else:
                body = deepcopy(op['values'])
                allowed = set(FIELDS[entity]) - {key, 'updated_at', 'created_by_candidate'}
                if set(body) - allowed - {'updated_at', 'created_by_candidate'}:
                    raise ValueError('Unsupported API fields')
                body = {k: v for k, v in body.items() if k in allowed}
                for k, v in list(body.items()):
                    if isinstance(v, dict):
                        if set(v) != {'created_account'} or v['created_account'] not in refs:
                            raise ValueError('Unresolved account reference')
                        body[k] = {'result_of': refs[v['created_account']], 'field': 'account_id'}
                if 'is_active' in body:
                    body['is_active'] = bool(body['is_active'])
                if not create and entity == 'accounts':
                    body.pop('parent_name', None)  # derived from parent_id by the CRM
            if not body:
                raise ValueError('Empty API operation')
            ordinal = len(output) + 1
            path = '/' + entity + (('/' + rid) if rid else '')
            output.append(dict(ordinal=ordinal, decision_id=decision['decision_id'], entity=entity,
                method='POST' if create else 'PATCH', path=path, url=BASE_URL + path, body=body,
                before=before, existing_ids=[r[0] for r in c.execute(f'SELECT {key} FROM crm_{entity}')] if create else []))
            if action == 'create':
                refs[op['ref']] = ordinal
            if before is not None:
                working[entity, rid].update(body)
                if entity == 'accounts' and 'parent_id' in body:
                    parent = c.execute('SELECT name FROM crm_accounts WHERE account_id=?', (body['parent_id'],)).fetchone()
                    working[entity, rid]['parent_name'] = parent['name'] if parent else ''

    def get_plan(self, pid, fingerprint):
        with closing(open_database(self.database)) as c:
            row = c.execute('SELECT * FROM production_plans WHERE plan_id=?', (pid,)).fetchone()
            if not row or fingerprint != row['digest']:
                raise ValueError('The saved API preview does not match this confirmation')
            plan = json.loads(row['plan_json'])
            if digest(plan) != fingerprint:
                raise ValueError('Saved API plan failed its integrity check')
            return dict(row), plan

    def progress(self, pid, ordinal, **values):
        values['updated_at'] = review.now()
        with closing(open_database(self.database)) as c, c:
            c.execute(f"UPDATE production_requests SET {','.join(k+'=?' for k in values)} WHERE plan_id=? AND ordinal=?",
                      (*values.values(), pid, ordinal))

    def resolve(self, value, rows):
        if isinstance(value, dict):
            if set(value) == {'result_of', 'field'}:
                result = rows[value['result_of']]
                if result['status'] != 'verified' or not result['remote_id']:
                    raise ValueError('A preceding account creation has not been verified')
                return result['remote_id']
            return {k: self.resolve(v, rows) for k, v in value.items()}
        return value

    def verify(self, req, rid, body, rows):
        actual = self.client.request('GET', '/' + req['entity'] + '/' + valid_id(rid))
        validate_record(req['entity'], actual)
        expected = self.resolve(req['before'], rows) if req['before'] else {}
        expected.update(body)
        expected[IDS[req['entity']]] = rid
        if req['entity'] == 'accounts' and 'parent_id' in body and req['method'] == 'PATCH':
            with closing(open_database(self.database)) as c:
                parent = c.execute('SELECT name FROM crm_accounts WHERE account_id=?', (body['parent_id'],)).fetchone()
            expected['parent_name'] = parent['name'] if parent else ''
        if any(actual.get(k) != v for k, v in business(expected).items()):
            raise ValueError('CRM read-back differs from the approved change. Submission paused for reconciliation.')
        return actual

    def execute(self, pid, fingerprint, recovery_ids=None):
        """Synchronous worker; HTTP polling can show durable progress concurrently.

        A lost POST response is never replayed. A human may supply its created ID;
        that ID must be new since preview and GET must match the approved body.
        """
        with self.exclusive():
            saved, plan = self.get_plan(pid, fingerprint)
            if saved['status'] == 'complete':
                return self.state()
            if saved['status'] not in ('preview', 'paused', 'running'):
                raise ValueError('Prepare a new API preview before confirming')
            if saved['status'] == 'preview':
                remote = self.load_remote()
                if snapshot_digest(remote) != plan['baseline']:
                    raise ValueError('CRM changed after preview. Prepare and review a fresh call list.')
                with closing(open_database(self.database)) as c, c:
                    c.execute('BEGIN IMMEDIATE'); review.require_idle(c)
                    self.check_decisions(c, plan)
                    c.execute("UPDATE production_plans SET status='running',error=NULL WHERE plan_id=?", (pid,))
            else:
                with closing(open_database(self.database)) as c, c:
                    self.check_decisions(c, plan)
                    c.execute("UPDATE production_plans SET status='running',error=NULL WHERE plan_id=?", (pid,))
            try:
                for req in plan['requests']:
                    with closing(open_database(self.database)) as c:
                        rows = {r['ordinal']: dict(r) for r in c.execute('SELECT * FROM production_requests WHERE plan_id=?', (pid,))}
                    status = rows[req['ordinal']]
                    if status['status'] == 'verified':
                        continue
                    body = self.resolve(req['body'], rows)
                    rid = status['remote_id']
                    if status['status'] in ('sending', 'uncertain') and not rid:
                        if req['method'] == 'POST':
                            supplied = (recovery_ids or {}).get(str(req['ordinal']))
                            if not supplied or supplied in req['existing_ids']:
                                self.progress(pid, req['ordinal'], status='uncertain', error='Unknown create outcome. Check the CRM and supply the newly created record ID; this POST will not be repeated.')
                                raise ValueError('A create request has an unknown outcome. Inspect the CRM and reconcile its ID before continuing.')
                            rid = valid_id(supplied)
                            actual = self.verify(req, rid, body, rows)
                            self.progress(pid, req['ordinal'], remote_id=rid, status='written')
                        else:
                            # A PATCH may have reached the server. Read and verify;
                            # do not replay it automatically after an ambiguous error.
                            rid = req['before'][IDS[req['entity']]]
                            actual = self.verify(req, rid, body, rows)
                    elif status['status'] not in ('written',):
                        if req['method'] == 'PATCH':
                            rid = req['before'][IDS[req['entity']]]
                            actual = self.client.request('GET', req['path'])
                            expected = self.resolve(req['before'], rows)
                            if business(actual) != business(expected):
                                raise ValueError('A target changed before its API call. Submission paused; no further writes were sent.')
                        else:
                            # An external create since preview must not silently duplicate our record.
                            created_here = {r['remote_id'] for r in rows.values() if r['status'] == 'verified'}
                            for row in self.client.list(req['entity']):
                                if row[IDS[req['entity']]] not in req['existing_ids'] and row[IDS[req['entity']]] not in created_here and row.get('name') == body.get('name'):
                                    if req['entity'] == 'accounts' or row.get('account_id') == body.get('account_id'):
                                        raise ValueError('A matching record was created after preview. Reconcile before continuing.')
                        self.progress(pid, req['ordinal'], status='sending', resolved_body=review.dumps(body), error=None)
                        try:
                            result = self.client.request(req['method'], req['path'], body)
                            rid = valid_id(result.get(IDS[req['entity']]))
                            if req['method'] == 'PATCH' and rid != req['before'][IDS[req['entity']]]:
                                raise APIError('CRM returned a different record ID')
                            self.progress(pid, req['ordinal'], status='written', remote_id=rid, response_json=review.dumps(result))
                        except Exception as error:
                            self.progress(pid, req['ordinal'], status='failed' if isinstance(error, APIError) and error.definite else 'uncertain', error=str(error))
                            raise
                    actual = self.verify(req, rid, body, rows)
                    # The mirror records exactly what GET confirmed, including server timestamps.
                    with closing(open_database(self.database)) as c, c:
                        upsert(c, req['entity'], actual)
                        c.execute("UPDATE production_requests SET status='verified',remote_id=?,after_json=?,error=NULL,updated_at=? WHERE plan_id=? AND ordinal=?",
                                  (rid, review.dumps(actual), review.now(), pid, req['ordinal']))
                with closing(open_database(self.database)) as c, c:
                    c.execute('BEGIN IMMEDIATE')
                    self.check_decisions(c, plan)
                    rows = {r['ordinal']: dict(r) for r in c.execute('SELECT * FROM production_requests WHERE plan_id=?', (pid,))}
                    for d in plan['decisions']:
                        before, after = [], []
                        for req in plan['requests']:
                            if req['decision_id'] != d['decision_id']:
                                continue
                            row = rows[req['ordinal']]; actual = json.loads(row['after_json'])
                            identity = {IDS[req['entity']]: row['remote_id']}
                            if req['entity'] == 'contacts':
                                identity['account_id'] = actual['account_id']
                            before.append(dict(identity, values=self.resolve(req['before'], rows)))
                            after.append(dict(identity, values=actual))
                        review.finish_decision(c, d, before, after)
                    auto_complete_matches(c)
                    c.execute("UPDATE production_plans SET status='complete',error=NULL,finished_at=? WHERE plan_id=?", (review.now(), pid))
            except Exception as error:
                with closing(open_database(self.database)) as c, c:
                    c.execute("UPDATE production_plans SET status='paused',error=? WHERE plan_id=?", (str(error), pid))
                raise
            return self.state()

    def cancel(self, pid, fingerprint):
        """Only a plan with no potentially successful writes may be discarded."""
        with self.exclusive():
            self.get_plan(pid, fingerprint)
            with closing(open_database(self.database)) as c, c:
                if c.execute("SELECT 1 FROM production_requests WHERE plan_id=? AND status NOT IN ('pending','failed')", (pid,)).fetchone():
                    raise ValueError('This plan has remote writes or uncertain outcomes. Reconcile it before editing decisions.')
                c.execute("UPDATE production_plans SET status='invalidated' WHERE plan_id=? AND status!='complete'", (pid,))

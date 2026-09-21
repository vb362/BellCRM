"""Local review transactions. No network access and no production synchronization."""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from uuid import uuid4

from supplemental_updates import administrator_operations, contact_plan, describe, phone_key, care_key, person_key
from duplicate_resolution import with_duplicate_contacts, preservation_operations

from normalize_data import open_database, CRM_FIELDS, normalize_records, normalize_name, normalize_street, normalize_text
from match_records import assert_proposal_applicable, proposal_review_state, dumps, now, address, corporate_ids, auto_complete_matches, AUTO_MATCH_NOTE

EDITABLE = {'name', 'billing_street', 'billing_city', 'billing_state', 'billing_zip', 'care_type', 'phone', 'status', 'note'}
STATUSES = {'Active', 'Inactive', 'Needs Review'}


def unpack(row):
    p = dict(row)
    for key in ('proposed_changes', 'supporting_evidence', 'approved_changes', 'before_values', 'after_values', 'errors'):
        if key in p and p[key] is not None:
            p[key] = json.loads(p[key])
    return p


def version(p):
    return hashlib.sha256(dumps([p['proposed_changes'], p['supporting_evidence'], p['depends_on_proposal_id']]).encode()).hexdigest()


def require_no_submission(c):
    if c.execute("SELECT 1 FROM sqlite_master WHERE name='production_plans'").fetchone():
        if c.execute("SELECT 1 FROM production_plans WHERE status IN ('running','paused')").fetchone():
            raise ValueError('Finish the saved production submission before changing decisions or starting a run.')


def require_idle(c):
    require_no_submission(c)
    if c.execute("SELECT 1 FROM runs WHERE status='running' AND run_type='pipeline'").fetchone():
        raise ValueError('The pipeline is running. Wait for it to finish before saving decisions.')


def require_revision(c, pid, revision):
    d = c.execute('SELECT * FROM decisions WHERE proposal_id=?', (pid,)).fetchone()
    actual = d['decided_at'] if d else None
    if actual != revision:
        raise ValueError('This decision changed in another window. Refresh before continuing.')
    return d


def validate_values(c, values):
    if not values or set(values) - (set(CRM_FIELDS) - {'account_id'}):
        raise ValueError('Invalid account fields')
    for key, val in values.items():
        if isinstance(val, (dict, list)):
            raise ValueError('Unresolved account value')
        if key == 'status' and val not in STATUSES:
            raise ValueError('Invalid account status')
        if key in ('parent_id', 'duplicate_of_account', 'chow_current_account') and val:
            if not c.execute('SELECT 1 FROM crm_accounts WHERE account_id=?', (val,)).fetchone():
                raise ValueError('Referenced CRM account no longer exists')


CONTACT_FIELDS = {'account_id', 'name', 'title', 'email', 'phone', 'is_active', 'created_by_candidate', 'updated_at'}


def validate_contact(c, values, allow_reference=False):
    if not values or set(values) - CONTACT_FIELDS:
        raise ValueError('Invalid contact fields')
    for key, value in values.items():
        if key in ('is_active', 'created_by_candidate'):
            if type(value) is not int or value not in (0, 1):
                raise ValueError('Invalid contact active flag')
        elif key == 'account_id' and isinstance(value, dict) and allow_reference:
            if set(value) != {'created_account'} or not isinstance(value['created_account'], str):
                raise ValueError('Invalid new account reference')
        elif value is not None and (not isinstance(value, str) or len(value) > 10000):
            raise ValueError('Invalid contact value')
    if 'account_id' in values and not isinstance(values['account_id'], dict):
        if not c.execute('SELECT 1 FROM crm_accounts WHERE account_id=?', (values['account_id'],)).fetchone():
            raise ValueError('Contact account no longer exists')


def resolve_administrator(p, ops, item):
    resolved = []
    for op in ops:
        if op['action'] != 'resolve_administrator':
            resolved.append(op)
            continue
        selection = item.get('administrator_resolution')
        if not isinstance(selection, dict) or selection.get('confirmed') is not True:
            raise ValueError('Choose the administrator and confirm which former contacts to deactivate.')
        pick = selection.get('contact_id')
        former = selection.get('deactivate_ids')
        if pick not in ['new', *op['candidate_ids']] or not isinstance(former, list) or any(not isinstance(x, str) for x in former):
            raise ValueError('Invalid administrator selection')
        if len(set(former)) != len(former) or set(former) - set(op['administrator_ids']) or pick in former:
            raise ValueError('Invalid former administrator selection')
        contacts = {c['contact_id']: c for c in p['supporting_evidence']['contacts']}
        resolved.extend(administrator_operations(op['account_id'], op['name'], contacts.get(pick), [contacts[cid] for cid in former]))
    return resolved


def make_operations(c, p, item):
    choice = item.get('choice')
    note = item.get('note', '')
    if not isinstance(note, str) or len(note) > 10000:
        raise ValueError('Reviewer note is invalid or too long')
    note = note.strip()
    if choice not in ('approved', 'rejected', 'reviewed'):
        raise ValueError('Invalid decision')
    ops = deepcopy(p['proposed_changes'])
    if p['classification'] == 'duplicate_resolution':
        if choice == 'rejected':
            return choice, [], note
        ids = ops[0]['account_ids']
        survivor = item.get('survivor')
        if not note:
            raise ValueError('Add a note explaining the duplicate decision')
        if survivor == 'both' and choice == 'reviewed':
            return choice, [], note
        if choice != 'approved' or survivor not in ids:
            raise ValueError('Choose one of the listed accounts or keep both')
        ops = preservation_operations(p, survivor, item.get('phone_account_id'), note)
    elif choice != 'approved':
        if choice == 'reviewed' and ops:
            raise ValueError('This proposal requires approval or rejection')
        return choice, [], note
    elif not ops:
        return 'reviewed', [], note
    elif item.get('manual_values') is not None:
        edits = item['manual_values']
        if not isinstance(edits, dict) or set(edits) - EDITABLE:
            raise ValueError('Only the displayed editable fields can be changed manually')
        if any(not isinstance(v, str) or len(v) > 10000 for v in edits.values()):
            raise ValueError('Invalid manual values')
        if any(k in edits and not edits[k].strip() for k in ('name', 'billing_street', 'billing_city', 'billing_state')):
            raise ValueError('Name and address cannot be empty')
        create = next((op for op in ops if op['action'] == 'create'), None)
        update = next((op for op in ops if op['action'] == 'update'), None)
        if create:
            create['values'].update(edits)
        elif update:
            # Keep mandatory ownership/status actions from the approved proposal.
            if 'status' in edits and 'status' in update['values'] and edits['status'] != update['values']['status']:
                raise ValueError('Status must agree with this proposal')
            update['values'].update(edits)
        else:
            raise ValueError('Manual edits are unavailable for this proposal')
    ops = resolve_administrator(p, ops, item)
    for op in ops:
        if op['action'] in ('create_contact', 'update_contact'):
            validate_contact(c, op['values'], allow_reference=True)
        if op['action'] in ('create', 'update'):
            vals = {k: v for k, v in op['values'].items() if not isinstance(v, dict)}
            if vals:
                validate_values(c, vals)
    return choice, ops, note


def stage(database, items):
    if not isinstance(items, list) or not items or len(items) > 500:
        raise ValueError('Select at least one proposal')
    if len({item.get('proposal_id') for item in items}) != len(items):
        raise ValueError('Duplicate proposal in request')
    with closing(open_database(database)) as c, c:
        c.execute('BEGIN IMMEDIATE')
        require_idle(c)
        for item in items:
            pid = item.get('proposal_id')
            row = c.execute('SELECT * FROM proposals WHERE proposal_id=?', (pid,)).fetchone()
            if not row:
                raise ValueError('Proposal not found')
            p = unpack(row)
            p = with_duplicate_contacts(c, p)
            if item.get('version') != version(p):
                raise ValueError('This proposal changed. Refresh and review it again.')
            old = require_revision(c, pid, item.get('revision'))
            if old and old['submitted_at']:
                raise ValueError('Submitted decisions cannot be changed')
            if p['classification'] == 'duplicate_resolution':
                c.execute('UPDATE proposals SET supporting_evidence=? WHERE proposal_id=?',
                          (dumps(p['supporting_evidence']), pid))
            if proposal_review_state(c, pid) != 'ready':
                raise ValueError('Resolve the duplicate first, or refresh this proposal.')
            if item.get('choice') != 'rejected':
                assert_proposal_applicable(c, pid)
            choice, ops, note = make_operations(c, p, item)
            # Replacing only unsubmitted decisions is permitted by the audit triggers.
            c.execute('''INSERT INTO decisions(decision_id,proposal_id,choice,approved_changes,reviewer_note,decided_at)
                VALUES (?,?,?,?,?,?) ON CONFLICT(proposal_id) DO UPDATE SET
                choice=excluded.choice,approved_changes=excluded.approved_changes,
                reviewer_note=excluded.reviewer_note,decided_at=excluded.decided_at''',
                (str(uuid4()), pid, choice, dumps(ops), note, now()))


def unstage(database, pid, revision):
    with closing(open_database(database)) as c, c:
        c.execute('BEGIN IMMEDIATE')
        require_idle(c)
        d = require_revision(c, pid, revision)
        if not d or d['submitted_at']:
            raise ValueError('Only staged decisions can be removed')
        c.execute('DELETE FROM decisions WHERE proposal_id=?', (pid,))


def apply_operations(c, ops):
    refs, before, after = {}, [], []
    for op in ops:
        action = op['action']
        if action in ('create_contact', 'update_contact'):
            values = {k: refs[v['created_account']] if isinstance(v, dict) else v for k, v in op['values'].items()}
            values['updated_at'] = now()
            validate_contact(c, values)
            if action == 'create_contact':
                cid = 'LOCAL-CONTACT-' + str(uuid4())
                if not values.get('name') or not values.get('account_id') or not values.get('title'):
                    raise ValueError('A new contact requires an account, name and role')
                before.append(dict(contact_id=cid, account_id=values['account_id'], values=None))
                values['contact_id'] = cid
                c.execute(f'INSERT INTO crm_contacts ({",".join(values)}) VALUES ({",".join("?" for _ in values)})', tuple(values.values()))
            else:
                cid = op['contact_id']
                row = c.execute('SELECT * FROM crm_contacts WHERE contact_id=? AND account_id=?', (cid, op['account_id'])).fetchone()
                if not row:
                    raise ValueError('Target contact no longer belongs to this account')
                before.append(dict(contact_id=cid, account_id=row['account_id'], values=dict(row)))
                c.execute(f'UPDATE crm_contacts SET {",".join(k+"=?" for k in values)} WHERE contact_id=?', (*values.values(), cid))
            row = dict(c.execute('SELECT * FROM crm_contacts WHERE contact_id=?', (cid,)).fetchone())
            after.append(dict(contact_id=cid, account_id=row['account_id'], values=row))
            continue
        if action == 'create':
            aid = 'LOCAL-' + str(uuid4())
            values = dict(op['values'], account_id=aid, updated_at=now())
            validate_values(c, {k: v for k, v in values.items() if k != 'account_id'})
            c.execute(f'INSERT INTO crm_accounts ({",".join(values)}) VALUES ({",".join("?" for _ in values)})', tuple(values.values()))
            refs[op['ref']] = aid
            before.append(dict(account_id=aid, values=None))
        else:
            aid = op['account_id']
            row = c.execute('SELECT * FROM crm_accounts WHERE account_id=?', (aid,)).fetchone()
            if not row:
                raise ValueError('Target account no longer exists')
            before.append(dict(account_id=aid, values=dict(row)))
            if action == 'append_note':
                values = {'note': '\n'.join(filter(None, [row['note'], op['text']]))}
            elif action == 'update':
                values = {k: refs[v['created_account']] if isinstance(v, dict) else v for k, v in op['values'].items()}
            else:
                raise ValueError('Unsupported operation')
            validate_values(c, values)
            # CHOW changes only the old link; the old account's other fields stay exact.
            if set(values) != {'chow_current_account'}:
                values['updated_at'] = now()
            c.execute(f'UPDATE crm_accounts SET {",".join(k+"=?" for k in values)} WHERE account_id=?', (*values.values(), aid))
        after.append(dict(account_id=aid, values=dict(c.execute('SELECT * FROM crm_accounts WHERE account_id=?', (aid,)).fetchone())))
    return before, after


def check_saved_operations(c, p, ops):
    """Also protect fields changed manually and new-account proposals with no target."""
    expected = p['supporting_evidence'].get('preconditions', {})
    if p['classification'] == 'duplicate_resolution':
        resolution = next((op for op in ops if op.get('duplicate_resolution')), None)
        if not resolution:
            raise ValueError('Review the duplicate again to include contact preservation and the facility phone.')
        selection = resolution['duplicate_resolution']
        wanted = preservation_operations(p, resolution['values']['duplicate_of_account'],
                                         selection.get('phone_account_id'),
                                         next(op['text'] for op in ops if op['action'] == 'append_note'))
        if ops != wanted:
            raise ValueError('Duplicate contact preservation changed. Review the duplicate again.')
    for op in ops:
        if op['action'] == 'update':
            actual = c.execute('SELECT * FROM crm_accounts WHERE account_id=?', (op['account_id'],)).fetchone()
            old = expected.get(op['account_id'])
            if not actual or not old or any(actual[k] != old[k] for k in op['values']):
                raise ValueError('An account field changed since review. Remove the staged decision and run the pipeline again.')
    if p['classification'] == 'create_account':
        records = normalize_records(c.execute('SELECT * FROM crm_accounts').fetchall(), crm=True)
        parents = corporate_ids(records)
        proposed = next(op['values'] for op in ops if op['action'] == 'create')
        target = normalize_records([proposed], crm=True)[0]
        for a in records:
            if a['account_id'] in parents or a['chow_current_account'] or a['duplicate_of_account']:
                continue
            if a['normalized_name'] == target['normalized_name'] or (address(target) and address(a) == address(target)):
                raise ValueError('A matching account now exists. Remove the staged creation and run the pipeline again.')


def submit(database, items):
    """All selected decisions commit together; a stale/conflicting one rolls all back."""
    from normalize_data import DATABASES
    from pathlib import Path
    if Path(database).resolve() == DATABASES['production'].resolve():
        raise ValueError('Production decisions require the API preview and executor')
    if not isinstance(items, list) or not items or len(items) > 500:
        raise ValueError('Select staged decisions to submit')
    if len({i.get('proposal_id') for i in items}) != len(items):
        raise ValueError('Duplicate proposal in submission')
    with closing(open_database(database)) as c, c:
        c.execute('BEGIN IMMEDIATE')
        require_idle(c)
        pending = []
        # Check gates before any writes: dependencies cannot be bypassed within a batch.
        for item in items:
            d = require_revision(c, item.get('proposal_id'), item.get('revision'))
            if not d:
                raise ValueError('Decision has not been staged')
            if d['submitted_at']:
                continue  # A network retry must not apply the same edits twice.
            if proposal_review_state(c, d['proposal_id']) != 'ready':
                raise ValueError('A proposal is blocked or superseded. Refresh before submitting.')
            pending.append(dict(d))
        for d in pending:
            pid = d['proposal_id']
            if d['choice'] != 'rejected':
                assert_proposal_applicable(c, pid)
            ops = json.loads(d['approved_changes'])
            if ops:
                proposal = unpack(c.execute('SELECT * FROM proposals WHERE proposal_id=?', (pid,)).fetchone())
                check_saved_operations(c, proposal, ops)
            before, after = apply_operations(c, ops) if ops else ([], [])
            finish_decision(c, d, before, after)
        auto_complete_matches(c)
        return len(pending)


def finish_decision(c, d, before, after):
    """Record verified results and release duplicate dependencies in one transaction."""
    pid = d['proposal_id']
    ops = json.loads(d['approved_changes'])
    stamp = now()
    c.execute('UPDATE decisions SET submitted_at=? WHERE decision_id=?', (stamp, d['decision_id']))
    if ops:
        c.execute('''INSERT INTO change_history(change_id,decision_id,attempted_at,result,before_values,after_values)
            VALUES (?,?,?,'succeeded',?,?)''', (str(uuid4()), d['decision_id'], stamp, dumps(before), dumps(after)))
    p = c.execute('SELECT classification FROM proposals WHERE proposal_id=?', (pid,)).fetchone()
    if p['classification'] == 'duplicate_resolution' and d['choice'] == 'approved':
        losers = [op['account_id'] for op in ops if op['action'] == 'update' and op['values'].get('duplicate_of_account')]
        for aid in losers:
            for target in c.execute('''SELECT p.proposal_id,d.submitted_at FROM proposals p
                LEFT JOIN decisions d USING(proposal_id) WHERE p.account_id=?''', (aid,)).fetchall():
                if target['submitted_at']:
                    continue
                c.execute('''INSERT INTO decisions(decision_id,proposal_id,choice,reviewer_note,decided_at,submitted_at)
                    VALUES (?,?,'rejected','Account resolved as a duplicate.',?,?)
                    ON CONFLICT(proposal_id) DO UPDATE SET choice='rejected',approved_changes='[]',
                    reviewer_note=excluded.reviewer_note,decided_at=excluded.decided_at,submitted_at=excluded.submitted_at''',
                    (str(uuid4()), target['proposal_id'], stamp, stamp))
        refresh_survivor_proposals(c, pid, ops)


def refresh_survivor_proposals(c, duplicate_id, ops):
    """Reprepare blocked survivor details against the approved consolidated record.

    Only rebase contacts that matched the duplicate's reviewed snapshot. Unrelated
    stale evidence remains stale, and already staged decisions are never rewritten.
    """
    resolution = next((op for op in ops if op.get('duplicate_resolution')), None)
    if not resolution:
        return
    survivor = resolution['values']['duplicate_of_account']
    phone_chosen = bool(resolution['duplicate_resolution'].get('phone_account_id'))
    duplicate = unpack(c.execute('SELECT * FROM proposals WHERE proposal_id=?', (duplicate_id,)).fetchone())
    original_contacts = duplicate['supporting_evidence']['contact_preconditions'][survivor]
    current_contacts = [dict(r) for r in c.execute('SELECT * FROM crm_contacts WHERE account_id=? ORDER BY contact_id', (survivor,))]
    current = dict(c.execute('SELECT * FROM crm_accounts WHERE account_id=?', (survivor,)).fetchone())
    for row in c.execute('''SELECT p.* FROM proposals p WHERE p.depends_on_proposal_id=? AND p.account_id=?
            AND NOT EXISTS (SELECT 1 FROM decisions d WHERE d.proposal_id=p.proposal_id)''', (duplicate_id, survivor)).fetchall():
        child = unpack(row)
        evidence = child['supporting_evidence']
        changes = child['proposed_changes']
        if evidence.get('contact_preconditions', {}).get(survivor) == original_contacts:
            changes[:] = [op for op in changes if op['action'] not in ('create_contact', 'update_contact', 'resolve_administrator')]
            evidence['contacts'] = current_contacts
            evidence['contact_preconditions'][survivor] = current_contacts
            admin = evidence.get('website', {}).get('administrator')
            if person_key(admin):
                changes.extend(contact_plan(survivor, admin, current_contacts))
        if phone_chosen:
            for op in changes:
                if op['action'] == 'update' and op['account_id'] == survivor:
                    op['values'].pop('phone', None)
            for saved in (evidence.get('crm'), evidence.get('preconditions', {}).get(survivor)):
                if saved is not None:
                    saved['phone'] = current['phone']
            evidence['duplicate_phone_reviewed'] = current['phone']
        changes[:] = [op for op in changes if op['action'] != 'update' or op['values']]
        # Describe the remaining review, without repeating an obsolete list of fields.
        if not any(op['action'] == 'create' for op in changes):
            name = (evidence.get('crm') or evidence['website'])['name']
            child['title'] = f'Update remaining details for “{name}”' if changes else f'No remaining changes for “{name}”'
            child['explanation'] = child['explanation'].split(' The website also supplies current ', 1)[0]
        child = describe(child)
        c.execute('UPDATE proposals SET proposed_changes=?,supporting_evidence=?,title=?,explanation=? WHERE proposal_id=?',
                  (dumps(changes), dumps(evidence), child['title'], child['explanation'], child['proposal_id']))


def complete_existing_matches(database):
    """Apply the no-change policy to an existing working queue at server startup."""
    with closing(open_database(database)) as c, c:
        c.execute('BEGIN IMMEDIATE')
        require_idle(c)
        return auto_complete_matches(c)


def comparison_values(record, crm=False):
    """Reuse pipeline normalization for display comparisons, preserving originals."""
    if record is None:
        return {}
    prefix = 'billing_' if crm else ''
    columns = {'name': ('name', normalize_name), 'street': (prefix + 'street', normalize_street),
               'city': (prefix + 'city', normalize_text), 'state': (prefix + 'state', normalize_text), 'phone': ('phone', phone_key),
               'care': ('care_type' if crm else 'care_offerings', lambda v: care_key(v if crm else json.loads(v or '[]'))),
               'administrator': ('administrator', person_key)}
    return {key: normalize(record[column]) for key, (column, normalize) in columns.items() if column in record}


def read_state(database):
    with closing(open_database(database)) as c, c:
        c.execute('BEGIN')
        runs = [unpack(r) for r in c.execute('SELECT * FROM runs ORDER BY started_at DESC')]
        proposals = []
        for row in c.execute('SELECT * FROM proposals ORDER BY created_at DESC,proposal_id'):
            p = unpack(row)
            p['review_state'] = proposal_review_state(c, p['proposal_id'])
            d = c.execute('SELECT * FROM decisions WHERE proposal_id=?', (p['proposal_id'],)).fetchone()
            if not d or not d['submitted_at']:
                p = with_duplicate_contacts(c, p)
            p['version'] = version(p)
            p['decision'] = unpack(d) if d else None
            evidence = p['supporting_evidence']
            operations = p['decision']['approved_changes'] if d else p['proposed_changes']
            proposed = next((op['values'] for op in operations if op['action'] == 'create'), None)
            if proposed is None:
                proposed = next((op['values'] for op in operations if op['action'] == 'update'), {})
            p['comparison_values'] = dict(
                website=comparison_values(evidence.get('website')),
                crm=comparison_values(evidence.get('crm'), crm=True),
                accounts={a['account_id']: comparison_values(a, crm=True) for a in evidence.get('accounts', [])},
                proposed=comparison_values(proposed, crm=True))
            if evidence.get('enrichment_version'):
                names = '; '.join(contact['name'] or '' for contact in evidence.get('contacts', [])
                                  if contact['is_active'] == 1 and person_key(contact['title']) == 'administrator')
                p['comparison_values']['crm']['administrator'] = person_key(names)
            if d:
                p['decision']['automatic'] = (p['classification'] == 'confident_match'
                    and d['choice'] == 'reviewed' and d['reviewer_note'] == AUTO_MATCH_NOTE)
            p['history'] = [unpack(h) for h in c.execute('SELECT * FROM change_history WHERE decision_id=? ORDER BY attempted_at', (d['decision_id'],))] if d else []
            if p['review_state'] in ('ready', 'decided') or d:
                proposals.append(p)
        sources = [dict(r) for r in c.execute('''SELECT source_url,name,fetched_at,snapshot_id FROM website_snapshots
            WHERE snapshot_id=(SELECT website_snapshot_id FROM runs WHERE scraping_status='complete'
                ORDER BY started_at DESC LIMIT 1) ORDER BY name,source_url''')]
        return dict(proposals=proposals, runs=runs, sources=sources,
                    accounts=[dict(r) for r in c.execute('SELECT * FROM crm_accounts ORDER BY name')],
                    contacts=[dict(r) for r in c.execute('SELECT * FROM crm_contacts ORDER BY name')])

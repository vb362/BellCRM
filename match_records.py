#!/usr/bin/env python3
"""Generate review proposals from one saved normalized run; never apply CRM edits.

    python3 match_records.py --database demo --run-id YOUR_RUN_ID

Run normalization first. The schema must be version 3 (migrate_databases.py).
All handoffs use SQLite. No API, scraping, CSV, or external service is used here.
"""

import argparse
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import sqlite3

from supplemental_updates import add_updates, suppress_decided, describe
from duplicate_resolution import with_duplicate_contacts
from proposal_copy import CHOW_CARD_LABEL, parent_sentence, proposal_copy, proposal_explanation
from uuid import uuid4

from normalize_data import DATABASES, CRM_FIELDS, batch_summary, open_database, normalize_name, normalize_street, normalize_text


AMBIGUITY = ('Multiple records share these matching details, so this interpretation '
             'depends on confirming that this is the corresponding account.')
ABSENCE_NOTE = ('Not found in the completed Bellhaven website scrape. Marked Inactive '
                'after human review; absence alone does not establish closure or a change of ownership.')
AUTO_MATCH_NOTE = 'Automatically approved: name, address, and parent match. No CRM changes needed.'
EXPLANATIONS = {
    'confident_match': "The matching name, address, and parent suggest the same facility. These CRM fields already agree with Bellhaven's current listing.",
    'name_correction': 'This is likely a name change or an outdated CRM name. The matching address connects the records, and the account already belongs to Bellhaven. The website supplies the current listed name.',
    'parent_correction': 'This is likely an ownership change or an outdated parent assignment. The name and address match a facility listed by Bellhaven. Because {finance}, the CHOW requirement to create a separate account does not apply.',
    'name_parent_correction': 'This is likely an ownership change with a rebrand. The address matches, but the facility name needs updating. {parent} Because {finance}, no separate CHOW account is required; the existing record can carry the current name and parent.',
    'address_correction': 'The matching name and parent suggest the same facility, with a possible move or an outdated CRM address. The website supplies its current listed address.',
    'address_parent_correction': 'The matching name suggests the same facility, but its address and ownership details may be outdated. If these records represent the same facility, the website supplies its current address and Bellhaven ownership. Because {finance}, the existing account can be updated.',
    'chow_address_match': 'This is likely an ownership change or an outdated parent assignment. The existing account has both revenue history and unpaid receivables, so billing requires it to be preserved. A new account represents the facility under Bellhaven, while the link connects it to the old billing record.',
    'chow_name_match': 'The matching name suggests a facility whose address and ownership details may have changed. If the identity is confirmed, its revenue history and unpaid receivables require preserving the old account. A separate account can hold the website address and Bellhaven parent without altering the old billing record.',
    'create_account': 'This facility appears to be missing from the CRM because neither its name nor its address matches an existing account. Creating it would capture a facility listed under Bellhaven. The reviewer should still check for an existing record with different details before approving creation.',
    'absent_from_website': "This account may no longer belong in Bellhaven's active facility list because it is absent from the completed website scrape. Making it Inactive preserves its history while reflecting that absence. The website alone does not establish whether it closed or changed ownership.",
}


def dumps(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def now():
    return datetime.now(timezone.utc).isoformat()


def proposal_key(identity):
    """Only semantic target/actions/values; never evidence, dates, or random IDs."""
    return hashlib.sha256(dumps(identity).encode('utf-8')).hexdigest()


def address(record):
    values = tuple(record['normalized_' + field] for field in ('street', 'city', 'state'))
    return values if all(values) else None


def website_address(website):
    return ', '.join(str(website[field]) for field in ('street', 'city', 'state', 'zip') if website[field])


def corporate_ids(crm):
    # The supplied CRM represents corporate entities as explicit Parent Accounts.
    # Incoming parent_id references also protect corporate entities with other names.
    return ({a['parent_id'] for a in crm if a['parent_id']}
            | {a['account_id'] for a in crm if '(parent account)' in (a['name'] or '').lower()})


def financial_rule(account):
    """Return direct/CHOW/unknown and the actual reason; a missing value is not zero."""
    values = []
    for field in ('lifetime_revenue', 'outstanding_ar'):
        try:
            value = Decimal(str(account[field]))
            values.append(value if value.is_finite() and value >= 0 else None)
        except (InvalidOperation, ValueError):
            values.append(None)
    revenue, ar = values
    if revenue == 0 and ar == 0:
        return 'direct', 'both lifetime revenue and outstanding AR are zero'
    if revenue == 0:
        return 'direct', 'lifetime revenue is zero'
    if ar == 0:
        return 'direct', 'outstanding AR is zero'
    if revenue is not None and ar is not None:
        return 'chow', 'lifetime revenue and outstanding AR are both greater than zero'
    return 'unknown', 'financial information is insufficient to decide'


def original(account):
    return {field: account[field] for field in CRM_FIELDS}


def website_values(website, parent):
    """Creation uses website details, never the previous account's billing history."""
    values = dict(name=website['name'], parent_id=parent['account_id'], parent_name=parent['name'],
                  billing_street=website['street'], billing_city=website['city'],
                  billing_state=website['state'], billing_zip=website['zip'],
                  phone=website['phone'], status='Active', lifetime_revenue=0,
                  outstanding_ar=0, created_by_candidate=1)
    # Preserve the website labels; do not invent a care taxonomy or change existing care fields.
    offerings = json.loads(website['care_offerings'] or '[]')
    if not isinstance(offerings, list) or not all(isinstance(item, str) for item in offerings):
        raise ValueError('Website care_offerings must be a list of strings')
    if offerings:
        values['care_type'] = '; '.join(offerings)
    return values


def issue(issues, code, message, **details):
    issues.append(dict(stage='matching', severity='warning', code=code, message=message, **details))


def eligible_accounts(crm, issues):
    """Exclude both kinds of linked old records; reject unsafe link graphs."""
    by_id = {a['account_id']: a for a in crm}
    parents = corporate_ids(crm)
    for account in crm:
        seen = set()
        current = account
        while True:
            aid = current['account_id']
            if aid in seen:
                raise ValueError(f'Circular CHOW/duplicate link involving {aid}')
            seen.add(aid)
            links = {current[f] for f in ('chow_current_account', 'duplicate_of_account') if current[f]}
            if len(links) > 1:
                raise ValueError(f'Conflicting CHOW/duplicate links on {aid}')
            if not links:
                break
            target = next(iter(links))
            if target not in by_id or target in parents:
                raise ValueError(f'Broken or non-facility CHOW/duplicate link on {aid}: {target}')
            current = by_id[target]
    return [a for a in crm if a['account_id'] not in parents
            and not a['chow_current_account'] and not a['duplicate_of_account']]


def evidence_for(run, website, account, candidates, siblings):
    return dict(format_version=1, run_id=run['run_id'], snapshot_id=run['website_snapshot_id'],
                website={k: v for k, v in website.items() if k != 'raw_html'} if website else None,
                crm=original(account) if account else None,
                crm_comparison={field: account[field] for field in ('normalized_name', 'normalized_street', 'normalized_city', 'normalized_state')} if account else None,
                preconditions={account['account_id']: original(account)} if account else {},
                candidate_account_ids=sorted(a['account_id'] for a in candidates),
                website_candidate_urls=sorted(w['source_url'] for w in siblings))


def make_proposal(classification, account, website, changes, title, bullets, explanation, evidence, dependency=None):
    title, evidence = proposal_copy(classification, title, dict(evidence, bullets=bullets))
    aid = account['account_id'] if account else None
    # Existing-account corrections are identified by the actual action, not the page URL.
    # The page URL identifies a create when no account ID exists yet.
    identity = dict(target=aid or (website['source_url'] if website else None), changes=changes)
    if not changes:
        identity['classification'] = classification
    return dict(proposal_key=proposal_key(identity), classification=classification, account_id=aid,
                source_url=website['source_url'] if website else None, proposed_changes=changes,
                title=title, explanation=proposal_explanation(explanation, evidence),
                supporting_evidence=evidence, depends_on_proposal_id=dependency)


def correction(run, website, account, parent, candidates, siblings, match_kind, issues):
    evidence = evidence_for(run, website, account, candidates, siblings)
    wname = website['name']
    if account is None:
        classification = 'create_account'
        title = f'Create a CRM account for “{wname}” under Bellhaven'
        bullets = ['No CRM name matches.', 'No CRM address matches.', 'Required website details are complete.']
        changes = [dict(action='create', ref='new_account', values=website_values(website, parent))]
        explanation = EXPLANATIONS[classification]
    else:
        aid = account['account_id']
        name_differs = account['normalized_name'] != website['normalized_name']
        parent_differs = account['parent_id'] != parent['account_id']
        finance, reason = financial_rule(account)
        if parent_differs and finance == 'unknown':
            issue(issues, 'insufficient_financial_data', 'Parent correction withheld: financial values are insufficient.', account_id=aid, source_url=website['source_url'])
            return None
        if parent_differs and finance == 'chow':
            classification = 'chow_address_match' if match_kind == 'address' else 'chow_name_match'
            title = f'{CHOW_CARD_LABEL} for “{wname}”'
            first = ('Addresses match; names ' + ('are missing in CRM.' if not account['name'] else 'differ.' if name_differs else 'match.')) if match_kind == 'address' else 'Names match, but addresses differ.'
            bullets = [first, parent_sentence(account), 'Lifetime revenue and outstanding AR are both greater than zero.']
            changes = [dict(action='create', ref='new_account', values=website_values(website, parent)),
                       dict(action='update', account_id=aid, values={'chow_current_account': {'created_account': 'new_account'}})]
        else:
            fields = {}
            parent_title = (f'Change parent of “{account["name"]}” from “{account["parent_name"] or account["parent_id"]}” to Bellhaven'
                            if account['parent_id'] else f'Set Bellhaven as the parent of “{account["name"]}”')
            finance_bullet = reason[0].upper() + reason[1:] + '.'
            if match_kind == 'name':
                fields = {f'billing_{field}': website[field] for field in ('street', 'city', 'state')
                          if account[f'normalized_{field}'] != website[f'normalized_{field}']}
                if website['zip'] and website['zip'] != account['billing_zip']:
                    fields['billing_zip'] = website['zip']
                classification = 'address_parent_correction' if parent_differs else 'address_correction'
                title = (f'Change the address of “{account["name"]}” to “{website_address(website)}” and its parent to Bellhaven'
                         if parent_differs else f'Change CRM address for “{account["name"]}” to “{website_address(website)}”')
                bullets = (['Names match, but addresses differ.', parent_sentence(account), finance_bullet]
                           if parent_differs else ['Names match.', 'Addresses differ; no CRM address matches.', 'CRM parent is Bellhaven.'])
            elif name_differs:
                fields['name'] = wname
                classification = 'name_parent_correction' if parent_differs else 'name_correction'
                name_title = (f'Rename “{account["name"]}” to “{wname}”' if account['name'] else f'Set CRM facility name to “{wname}”')
                title = (name_title + ' and ' + ('change its parent to Bellhaven' if account['parent_id'] else 'set Bellhaven as its parent') if parent_differs
                         else f'Change CRM facility name from “{account["name"]}” to “{wname}”' if account['name'] else name_title)
                bullets = (['Addresses match, but names differ or CRM name is missing.', parent_sentence(account), finance_bullet]
                           if parent_differs else ['Addresses match.', 'Names differ or CRM name is missing.', 'CRM parent is Bellhaven.'])
            elif parent_differs:
                classification, title = 'parent_correction', parent_title
                bullets = ['Names and addresses match.', parent_sentence(account), finance_bullet]
            else:
                classification = 'confident_match'
                title = f'No name, address, or parent change needed for “{account["name"]}”'
                bullets = ['Names match.', 'Addresses match.', 'CRM parent is Bellhaven.']
            if parent_differs:
                fields.update(parent_id=parent['account_id'], parent_name=parent['name'])
            changes = [dict(action='update', account_id=aid, values=fields)] if fields else []
        explanation = EXPLANATIONS[classification].format(finance=reason, parent=parent_sentence(account))
    ambiguity = []
    if len(candidates) > 1:
        ambiguity.append(f'{len(candidates)} CRM candidates share this {match_kind}')
    if len(siblings) > 1:
        ambiguity.append(f'{len(siblings)} website facilities share this address')
    if ambiguity:
        bullets.append('; '.join(ambiguity) + '.')
        explanation += ' ' + AMBIGUITY
    return make_proposal(classification, account, website, changes, title, bullets, explanation, evidence)


def duplicate_proposal(run, website, group):
    ids = sorted(a['account_id'] for a in group)
    evidence = evidence_for(run, website, None, group, [website])
    evidence['accounts'] = [original(a) for a in sorted(group, key=lambda a: a['account_id'])]
    evidence['preconditions'] = {a['account_id']: original(a) for a in group}
    evidence['requires_survivor_selection'] = True
    p = make_proposal('duplicate_resolution', None, website,
                      [dict(action='resolve_duplicates', account_ids=ids, survivor_account_id=None,
                            loser_status='Inactive', link_field='duplicate_of_account', requires_reviewer_note=True)],
                      f'Resolve Possible Duplicate - “{website["name"]}”',
                      ['Website facility matches multiple CRM records.',
                       'These CRM records have the same normalized name and address.',
                       'These are separate CRM account IDs.'],
                      'These records may be duplicate copies of the same facility. Choosing a survivor preserves one current record while retaining the other records as Inactive copies linked to it. Financial values and update dates help inform the choice; the reviewer makes the final decision.', evidence)
    # The question is about this group, independent of evidence or website changes.
    p['proposal_key'] = proposal_key(dict(action='resolve_duplicates', account_ids=ids))
    return p


def build_proposals(run, crm, website):
    """Pure matching: return descriptions and issues; no database access or writes."""
    issues = []
    eligible = eligible_accounts(crm, issues)
    parent_candidates = [a for a in crm if a['account_id'] in corporate_ids(crm)
                         and a['normalized_name'] == 'bellhaven senior living (parent account)']
    if len(parent_candidates) != 1:
        raise ValueError('Expected exactly one Bellhaven corporate parent account')
    parent = parent_candidates[0]
    by_address, by_name, web_addresses = defaultdict(list), defaultdict(list), defaultdict(list)
    for a in eligible:
        if address(a):
            by_address[address(a)].append(a)
        if a['normalized_name']:
            by_name[a['normalized_name']].append(a)
    for w in website:
        if address(w):
            web_addresses[address(w)].append(w)
    results, groups, touched = [], {}, set()
    incomplete = False
    for w in website:
        if not w['normalized_name'] or not address(w):
            incomplete = True
            issue(issues, 'incomplete_website_record', 'Website name, street, city, or state is missing; no proposal generated.', source_url=w['source_url'])
            continue
        candidates = by_address[address(w)]
        kind = 'address'
        if not candidates:
            candidates, kind = by_name[w['normalized_name']], 'name'
        # Protect name candidates from false absence even when address matching takes precedence.
        touched.update(a['account_id'] for a in candidates + by_name[w['normalized_name']])
        siblings = web_addresses[address(w)]
        grouped = defaultdict(list)
        for a in candidates:
            if a['normalized_name'] and address(a):
                grouped[(a['normalized_name'], address(a))].append(a)
        dependencies = {}
        for group in grouped.values():
            if len(group) < 2:
                continue
            duplicate = duplicate_proposal(run, w, group)
            groups.setdefault(duplicate['proposal_key'], duplicate)
            for a in group:
                dependencies[a['account_id']] = duplicate['proposal_key']
        for a in candidates or [None]:
            p = correction(run, w, a, parent, candidates, siblings, kind, issues)
            if p:
                p['dependency_key'] = dependencies.get(a['account_id']) if a else None
                results.append(p)
    # With an incomplete website record, there may be no reliable way to identify
    # the corresponding account. Conservatively withhold all absence proposals.
    if incomplete:
        issue(issues, 'absence_check_withheld', 'Absence proposals withheld because website records are incomplete.')
    else:
        for a in eligible:
            if a['parent_id'] != parent['account_id'] or a['account_id'] in touched or a['status'] == 'Inactive':
                continue
            changes = [dict(action='update', account_id=a['account_id'], values={'status': 'Inactive'}),
                       dict(action='append_note', account_id=a['account_id'], text=ABSENCE_NOTE)]
            p = make_proposal('absent_from_website', a, None, changes,
                              f'Mark “{a["name"]}” Inactive — not found on Bellhaven\'s website',
                              ['No match found on the website by either name or full address (Street + City + State).', 'CRM parent of the record is Bellhaven.'],
                              EXPLANATIONS['absent_from_website'], evidence_for(run, None, a, [], []))
            results.append(p)
    return list(groups.values()) + results, issues


def load_batch(connection, run):
    if run['scraping_status'] != 'complete' or run['normalization_status'] != 'complete':
        raise ValueError('This run must have complete scraping and normalization')
    batch_summary(connection, run, reused=True)
    if run['locations_found'] != run['locations_saved']:
        raise ValueError('Scrape counts are inconsistent')
    crm = [dict(r) for r in connection.execute('SELECT * FROM normalized_crm_accounts WHERE run_id = ? ORDER BY account_id', (run['run_id'],))]
    website = [dict(r) for r in connection.execute('''SELECT n.*, w.fetched_at, w.name, w.street, w.city, w.state, w.zip,
        w.care_offerings, w.phone, w.administrator FROM normalized_website_locations n
        JOIN website_snapshots w ON w.snapshot_id=n.snapshot_id AND w.source_url=n.source_url
        WHERE n.run_id=? ORDER BY n.source_url''', (run['run_id'],))]
    if len(website) != run['website_records_normalized']:
        raise ValueError('Website snapshot rows are missing')
    current = {r['account_id']: dict(r) for r in connection.execute('SELECT * FROM crm_accounts')}
    if {a['account_id']: original(a) for a in crm} != current:
        raise ValueError('CRM changed since normalization. Use a fresh run rather than stale evidence.')
    return crm, website


def enrich_proposals(connection, proposals):
    results = []
    for base in proposals:
        base = with_duplicate_contacts(connection, base)
        if not base['supporting_evidence'].get('website') or base['classification'] == 'duplicate_resolution':
            results.append(base)
            continue
        aid = base['account_id']
        contacts = [dict(r) for r in connection.execute(
            'SELECT * FROM crm_contacts WHERE account_id=? ORDER BY contact_id', (aid,))] if aid else []
        p = add_updates(base, contacts)
        identity = lambda item: dict(target=item['account_id'] or item['source_url'], changes=item['proposed_changes'],
                                    **({'classification': item['classification']} if not item['proposed_changes'] else {}))
        p['proposal_key'] = proposal_key(identity(p))
        # A staged decision keeps exactly the evidence and writes already reviewed.
        staged = connection.execute("""SELECT p.* FROM proposals p JOIN decisions d USING(proposal_id)
            WHERE p.account_id IS ? AND p.source_url IS ? AND p.classification<>'duplicate_resolution'
            AND d.submitted_at IS NULL LIMIT 1""", (aid, p['source_url'])).fetchone()
        if staged:
            saved = dict(staged)
            for field in ('proposed_changes', 'supporting_evidence'):
                saved[field] = json.loads(saved[field])
            saved['dependency_key'] = p.get('dependency_key')
            results.append(saved)
            continue
        exact = connection.execute('SELECT 1 FROM proposals WHERE proposal_key=?', (p['proposal_key'],)).fetchone()
        if not exact:
            previous = connection.execute("""SELECT p.* FROM proposals p JOIN decisions d USING(proposal_id)
                WHERE p.account_id IS ? AND (p.account_id IS NOT NULL OR p.source_url IS ?)
                AND p.classification<>'duplicate_resolution' AND d.submitted_at IS NOT NULL""",
                (aid, p['source_url'])).fetchall()
            before = dumps(p['proposed_changes'])
            p = suppress_decided(p, previous)
            if p is None or (before != '[]' and not p['proposed_changes']):
                # Reuse the settled question for run accounting, never reopen it.
                if previous:
                    saved = dict(previous[-1])
                    for field in ('proposed_changes', 'supporting_evidence'):
                        saved[field] = json.loads(saved[field])
                    saved['dependency_key'] = base.get('dependency_key')
                    results.append(saved)
                continue
            if dumps(p['proposed_changes']) != before:
                p['supporting_evidence']['decided_changes_omitted'] = True
            p['proposal_key'] = proposal_key(identity(p))
        # Hide obsolete unreviewed cards; never replace a human draft or decision.
        p['supporting_evidence']['supersedes'] = [r['proposal_id'] for r in connection.execute("""
            SELECT p.proposal_id FROM proposals p WHERE p.account_id IS ? AND p.source_url IS ?
            AND p.classification<>'duplicate_resolution' AND p.proposal_key<>?
            AND NOT EXISTS (SELECT 1 FROM decisions d WHERE d.proposal_id=p.proposal_id)
            """, (aid, p['source_url'], p['proposal_key']))]
        results.append(describe(p))
    return results


def save_proposals(connection, run_id, proposals):
    counts = dict(new_proposals=0, existing_proposals=0, decided_proposals_skipped=0)
    seen, ids = set(), {}
    for p in proposals:
        key = p['proposal_key']
        if key in seen:
            continue
        seen.add(key)
        dependency = ids.get(p.get('dependency_key'))
        row = connection.execute('SELECT * FROM proposals WHERE proposal_key=?', (key,)).fetchone()
        if row is None and p['classification'] in ('address_correction', 'address_parent_correction'):
            # Older decisions may include unchanged address fields. Removing those
            # redundant writes must not ask the reviewer the same question again.
            for previous in connection.execute('''SELECT p.* FROM proposals p JOIN decisions d USING(proposal_id)
                    WHERE p.account_id=? AND p.classification=? AND d.submitted_at IS NOT NULL''',
                    (p['account_id'], p['classification'])):
                old_changes = json.loads(previous['proposed_changes'])
                old_crm = json.loads(previous['supporting_evidence'])['crm']
                normalizers = {'billing_street': normalize_street, 'billing_city': normalize_text,
                               'billing_state': normalize_text, 'billing_zip': lambda v: v}
                for op in old_changes:
                    if op['action'] == 'update':
                        op['values'] = {k: v for k, v in op['values'].items()
                                        if k not in normalizers or normalizers[k](v) != normalizers[k](old_crm[k])}
                if old_changes == p['proposed_changes']:
                    row = previous
                    break
        if row:
            pid = row['proposal_id']
            # Staged decisions are still editable; only submission settles a proposal.
            decision = connection.execute('SELECT submitted_at FROM decisions WHERE proposal_id=?', (pid,)).fetchone()
            decided = decision is not None and decision['submitted_at'] is not None
            counts['decided_proposals_skipped' if decided else 'existing_proposals'] += 1
            if decision is None:
                # A formerly superseded question may become current again when
                # website data reverts. Remove obsolete reverse edges, avoiding cycles.
                for other in connection.execute('SELECT proposal_id,supporting_evidence FROM proposals WHERE proposal_id<>? AND NOT EXISTS (SELECT 1 FROM decisions d WHERE d.proposal_id=proposals.proposal_id)', (pid,)).fetchall():
                    context = json.loads(other['supporting_evidence'])
                    if pid in context.get('supersedes', []):
                        context['supersedes'].remove(pid)
                        connection.execute('UPDATE proposals SET supporting_evidence=? WHERE proposal_id=?', (dumps(context), other['proposal_id']))
                # Reuse the question, refreshing its still-unreviewed context. A
                # staged/submitted decision retains the evidence actually reviewed.
                connection.execute('UPDATE proposals SET title=?,explanation=?,supporting_evidence=?,source_url=? WHERE proposal_id=?',
                                   (p['title'], p['explanation'], dumps(p['supporting_evidence']), p['source_url'], pid))
            if dependency and not decided:
                # A new run can discover a duplicate for a previously unblocked correction.
                connection.execute('UPDATE proposals SET depends_on_proposal_id=? WHERE proposal_id=?', (dependency, pid))
        else:
            pid = str(uuid4())
            connection.execute('''INSERT INTO proposals
                (proposal_id,proposal_key,classification,account_id,source_url,proposed_changes,
                 explanation,supporting_evidence,created_at,title,depends_on_proposal_id)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                (pid, key, p['classification'], p['account_id'], p['source_url'], dumps(p['proposed_changes']),
                 p['explanation'], dumps(p['supporting_evidence']), now(), p['title'], dependency))
            counts['new_proposals'] += 1
        ids[key] = pid
        if p['classification'] == 'duplicate_resolution':
            # Also block older, still-pending corrections whose suggested values
            # differ from this run's. An old browser card must not bypass a newly
            # discovered duplicate merely because its proposal key is different.
            for aid in p['supporting_evidence']['candidate_account_ids']:
                connection.execute('''UPDATE proposals SET depends_on_proposal_id=?
                    WHERE account_id=? AND classification<>'duplicate_resolution'
                    AND NOT EXISTS (SELECT 1 FROM decisions d WHERE d.proposal_id=proposals.proposal_id
                                    AND d.submitted_at IS NOT NULL)''', (pid, aid))
        connection.execute('INSERT OR IGNORE INTO run_proposals(run_id,proposal_id) VALUES (?,?)', (run_id, pid))
    return counts


def match_database(database_path, run_id):
    """Atomic proposal batch with repeat-run reuse and a separate failure log."""
    connection = open_database(database_path)
    try:
        if connection.execute('PRAGMA user_version').fetchone()[0] not in (3, 4):
            raise ValueError('Matching requires schema version 3. Run migrate_databases.py first.')
        connection.execute('BEGIN IMMEDIATE')
        run = connection.execute('SELECT * FROM runs WHERE run_id=?', (run_id,)).fetchone()
        if run is None:
            raise ValueError(f'Run does not exist: {run_id}')
        if run['matching_status'] == 'complete':
            total = sum(run[k] for k in ('new_proposals', 'existing_proposals', 'decided_proposals_skipped'))
            if connection.execute('SELECT COUNT(*) FROM run_proposals WHERE run_id=?', (run_id,)).fetchone()[0] != total:
                raise ValueError('Completed matching batch is inconsistent')
            connection.rollback()
            return dict(run_id=run_id, reused=True, **{k: run[k] for k in ('new_proposals', 'existing_proposals', 'decided_proposals_skipped')})
        if connection.execute('SELECT 1 FROM run_proposals WHERE run_id=?', (run_id,)).fetchone():
            raise ValueError('An unfinished matching batch already has proposals; investigate before retrying')
        errors = json.loads(run['errors'])
        if not isinstance(errors, list) or not all(isinstance(e, dict) for e in errors):
            raise ValueError('Run errors must be a list of error records')
        errors = [e for e in errors if e.get('stage') != 'matching']
        connection.execute("UPDATE runs SET matching_status='running', current_stage='matching' WHERE run_id=?", (run_id,))
        connection.execute('SAVEPOINT matching_batch')
        try:
            crm, website = load_batch(connection, run)
            proposals, issues = build_proposals(run, crm, website)
            proposals = enrich_proposals(connection, proposals)
            counts = save_proposals(connection, run_id, proposals)
            auto_complete_matches(connection, run_id)
            connection.execute('''UPDATE runs SET matching_status='complete', current_stage='complete',
                new_proposals=?,existing_proposals=?,decided_proposals_skipped=?,errors=? WHERE run_id=?''',
                (*counts.values(), dumps(errors + issues), run_id))
            connection.commit()
            return dict(run_id=run_id, reused=False, issues=issues, **counts)
        except (Exception, KeyboardInterrupt) as error:
            try:
                connection.execute('ROLLBACK TO matching_batch')
                connection.execute('RELEASE matching_batch')
                errors.append(dict(stage='matching', severity='error', code='matching_failed', message=str(error) or type(error).__name__))
                connection.execute("UPDATE runs SET matching_status='failed', new_proposals=0, existing_proposals=0, decided_proposals_skipped=0, errors=? WHERE run_id=?", (dumps(errors), run_id))
                connection.commit()
            except sqlite3.Error:
                connection.rollback()
            raise
    finally:
        connection.close()


def proposal_review_state(connection, proposal_id):
    """Read-only gate for the future UI/approval backend. 'ready' is not approval.

    Call again inside the application transaction, then check preconditions before
    writing CRM changes. A rejected duplicate question is NOT a keep-both choice.
    Explicit keep-both uses a submitted 'reviewed' decision with no changes.
    """
    p = connection.execute('SELECT * FROM proposals WHERE proposal_id=?', (proposal_id,)).fetchone()
    if p is None:
        raise ValueError('Proposal does not exist')
    if connection.execute('SELECT 1 FROM decisions WHERE proposal_id=? AND submitted_at IS NOT NULL', (proposal_id,)).fetchone():
        return 'decided'
    for newer in connection.execute('SELECT supporting_evidence FROM proposals WHERE proposal_id<>?', (proposal_id,)):
        if proposal_id in json.loads(newer['supporting_evidence']).get('supersedes', []):
            return 'superseded'
    if p['classification'] in ('name_correction', 'name_parent_correction'):
        evidence = json.loads(p['supporting_evidence'])
        crm_name = normalize_name(evidence['crm']['name'])
        if crm_name and crm_name == normalize_name(evidence['website']['name']):
            # Old batches retain their original evidence. A newer normalization
            # rule can make their rename unnecessary; never apply that old card.
            # The next run prepares any remaining parent correction separately.
            return 'superseded'
    if p['classification'] in ('address_correction', 'address_parent_correction'):
        original_crm = json.loads(p['supporting_evidence'])['crm']
        normalizers = {'billing_street': normalize_street, 'billing_city': normalize_text,
                       'billing_state': normalize_text, 'billing_zip': lambda v: v}
        for op in json.loads(p['proposed_changes']):
            if op['action'] == 'update':
                for field, value in op['values'].items():
                    if field in normalizers and normalizers[field](value) == normalizers[field](original_crm[field]):
                        # Keep the old evidence, but require a fresh proposal that
                        # changes only genuinely different address fields.
                        return 'superseded'
    if p['classification'] == 'duplicate_resolution':
        members = set(json.loads(p['supporting_evidence'])['candidate_account_ids'])
        # A newly discovered third copy changes the group. Do not leave the old
        # two-account question actionable alongside the more complete question.
        for other in connection.execute("SELECT supporting_evidence FROM proposals WHERE classification='duplicate_resolution' AND proposal_id<>?", (proposal_id,)):
            if members < set(json.loads(other['supporting_evidence']).get('candidate_account_ids', [])):
                return 'superseded'
        for aid in members:
            a = connection.execute('SELECT duplicate_of_account,chow_current_account FROM crm_accounts WHERE account_id=?', (aid,)).fetchone()
            if not a or a['duplicate_of_account'] or a['chow_current_account']:
                return 'superseded'
    if p['account_id']:
        a = connection.execute('SELECT duplicate_of_account,chow_current_account FROM crm_accounts WHERE account_id=?', (p['account_id'],)).fetchone()
        if not a or a['duplicate_of_account'] or a['chow_current_account']:
            return 'superseded'
    dependency = p['depends_on_proposal_id']
    if dependency:
        decision = connection.execute('SELECT * FROM decisions WHERE proposal_id=? AND submitted_at IS NOT NULL', (dependency,)).fetchone()
        if not decision:
            return 'blocked'
        if decision['choice'] == 'reviewed' and json.loads(decision['approved_changes']) == []:
            return 'ready'
        if decision['choice'] != 'approved' or not connection.execute("SELECT 1 FROM change_history WHERE decision_id=? AND result='succeeded'", (decision['decision_id'],)).fetchone():
            return 'blocked'
    return 'ready'


def assert_proposal_applicable(connection, proposal_id):
    """Guard for a future approval transaction; performs no writes itself.

    The application must hold BEGIN IMMEDIATE, call this guard, then apply its
    validated operations and history in that same transaction. Changes to phone,
    notes, or updated_at alone do not invalidate a name/parent decision.
    """
    if not connection.in_transaction:
        raise ValueError('Check applicability inside the approval transaction')
    state = proposal_review_state(connection, proposal_id)
    if state != 'ready':
        raise ValueError(f'Proposal cannot be applied: {state}')
    p = connection.execute('SELECT * FROM proposals WHERE proposal_id=?', (proposal_id,)).fetchone()
    evidence = json.loads(p['supporting_evidence'])
    changes = json.loads(p['proposed_changes'])
    relevant = {'name', 'parent_id', 'billing_street', 'billing_city', 'billing_state',
                'status', 'lifetime_revenue', 'outstanding_ar', 'chow_current_account', 'duplicate_of_account'}
    if p['classification'] == 'duplicate_resolution':
        relevant.add('phone')
    for op in changes:
        if op['action'] == 'update':
            relevant.update(op['values'])
    for aid, expected in evidence.get('preconditions', {}).items():
        actual = connection.execute('SELECT * FROM crm_accounts WHERE account_id=?', (aid,)).fetchone()
        if not actual or any(actual[field] != expected[field] for field in relevant):
            raise ValueError(f'Proposal is stale: relevant CRM data changed for {aid}')
    for aid, expected in evidence.get('contact_preconditions', {}).items():
        actual = [dict(r) for r in connection.execute('SELECT * FROM crm_contacts WHERE account_id=? ORDER BY contact_id', (aid,))]
        if actual != expected:
            raise ValueError(f'Proposal is stale: contacts changed for {aid}. Run the pipeline again.')
    return dict(p)


def auto_complete_matches(connection, run_id=None):
    """Record eligible no-change matches without human review or CRM writes.

    Run after all proposal dependencies are saved, and after duplicate decisions.
    Existing human decisions (including drafts) are never overwritten.
    """
    if not connection.in_transaction:
        raise ValueError('Automatic completion requires a transaction')
    rows = connection.execute('''SELECT p.proposal_id FROM proposals p
        WHERE p.classification='confident_match' AND json_array_length(p.proposed_changes)=0
        AND NOT EXISTS (SELECT 1 FROM decisions d WHERE d.proposal_id=p.proposal_id)
        AND (? IS NULL OR EXISTS (SELECT 1 FROM run_proposals rp
            WHERE rp.proposal_id=p.proposal_id AND rp.run_id=?))''', (run_id, run_id)).fetchall()
    completed = 0
    for row in rows:
        try:
            assert_proposal_applicable(connection, row['proposal_id'])
        except ValueError:
            # In particular, never bypass a pending/rejected duplicate decision,
            # or record an old match whose relevant CRM fields have changed.
            continue
        stamp = now()
        connection.execute('''INSERT INTO decisions
            (decision_id,proposal_id,choice,approved_changes,reviewer_note,decided_at,submitted_at)
            VALUES (?,?,'reviewed','[]',?,?,?)''',
            (str(uuid4()), row['proposal_id'], AUTO_MATCH_NOTE, stamp, stamp))
        completed += 1
    return completed


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--database', required=True, choices=DATABASES)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    try:
        result = match_database(DATABASES[args.database], args.run_id)
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(1, f'Matching stopped: {error}\n')
    except KeyboardInterrupt:
        parser.exit(130, 'Matching interrupted. No partial proposal batch was saved.\n')
    print('Reused completed matching batch.' if result['reused'] else 'Saved matching proposals.')
    for key in ('run_id', 'new_proposals', 'existing_proposals', 'decided_proposals_skipped'):
        print(f'{key}: {result[key]}')
    print(f"Issues recorded in Runs: {len(result.get('issues', []))}")
    print('CRM accounts are unchanged. No API calls were made.')


if __name__ == '__main__':
    main()

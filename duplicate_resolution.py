"""Duplicate review evidence and contact preservation using verified API operations."""
from copy import deepcopy

from supplemental_updates import phone_key


def with_duplicate_contacts(c, proposal):
    """Snapshot all linked contacts; also supports old, not-yet-submitted cards."""
    if proposal['classification'] != 'duplicate_resolution':
        return proposal
    if proposal['supporting_evidence'].get('duplicate_resolution_version') == 1:
        return proposal
    p = deepcopy(proposal)
    evidence = p['supporting_evidence']
    ids = p['proposed_changes'][0]['account_ids']
    evidence['duplicate_resolution_version'] = 1
    evidence['contact_preconditions'] = {
        aid: [dict(row) for row in c.execute(
            'SELECT * FROM crm_contacts WHERE account_id=? ORDER BY contact_id', (aid,))]
        for aid in ids
    }
    evidence['contacts'] = [contact for group in evidence['contact_preconditions'].values() for contact in group]
    return p


def preservation_operations(p, survivor, phone_account_id, note):
    evidence = p['supporting_evidence']
    if evidence.get('duplicate_resolution_version') != 1:
        raise ValueError('Review the duplicate again to include contact preservation and the facility phone.')
    accounts = {a['account_id']: a for a in evidence['accounts']}
    conflict = len({phone_key(a.get('phone')) for a in accounts.values()}) > 1
    if phone_account_id is not None and phone_account_id not in accounts:
        raise ValueError('Choose a facility phone from one of the duplicate accounts.')
    if conflict and not phone_account_id:
        raise ValueError('Choose which facility phone to keep on the survivor.')
    ops = []
    for contact in evidence['contacts']:
        if contact['account_id'] == survivor:
            continue
        if not contact['name'] or not contact['title'] or contact['is_active'] not in (0, 1):
            raise ValueError('A contact on the duplicate is missing a name, role or active status. Correct it before resolving duplicates.')
        values = {key: contact[key] for key in ('name', 'title', 'email', 'phone', 'is_active')}
        values.update(account_id=survivor, created_by_candidate=1)
        ops.append(dict(action='create_contact', values=values, source_contact_id=contact['contact_id']))
        if contact['is_active'] == 1:
            ops.append(dict(action='update_contact', contact_id=contact['contact_id'],
                            account_id=contact['account_id'], values={'is_active': 0}))
    if phone_account_id and accounts[survivor].get('phone') != accounts[phone_account_id].get('phone'):
        ops.append(dict(action='update', account_id=survivor, values={'phone': accounts[phone_account_id].get('phone')}))
    for aid in accounts:
        if aid != survivor:
            ops.extend([
                dict(action='update', account_id=aid, values=dict(status='Inactive', duplicate_of_account=survivor),
                     duplicate_resolution=dict(version=1, phone_account_id=phone_account_id)),
                dict(action='append_note', account_id=aid, text=note),
            ])
    return ops

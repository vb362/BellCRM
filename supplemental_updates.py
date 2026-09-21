"""Website enrichment after identity matching; never writes CRM records."""
from copy import deepcopy
import json
import re
import unicodedata


def person_key(value):
    return ' '.join(unicodedata.normalize('NFKC', value or '').casefold().split())


def phone_key(value):
    text = (value or '').strip()
    parts = re.split(r'\s*(?:ext\.?|extension|x|#)\s*', text, maxsplit=1, flags=re.I)
    number = re.sub(r'\D', '', parts[0])
    if len(number) == 11 and number.startswith('1'):
        number = number[1:]
    return number + ('x' + re.sub(r'\D', '', parts[1]) if len(parts) > 1 else '')


def care_key(value):
    items = value if isinstance(value, list) else re.split(r'[;,\n]+', value or '')
    return sorted({person_key(v) for v in items if person_key(v)})


def contact_plan(account_id, name, contacts):
    """Names use person normalization, never facility-name substitutions."""
    active = [c for c in contacts if c['is_active'] == 1 and person_key(c['title']) == 'administrator']
    same = [c for c in contacts if person_key(c['name']) == person_key(name)]
    if len(active) == 1 and person_key(active[0]['name']) == person_key(name):
        return []
    if len(same) > 1 or len(active) > 1:
        return [dict(action='resolve_administrator', account_id=account_id, name=name,
                     candidate_ids=[c['contact_id'] for c in same],
                     administrator_ids=[c['contact_id'] for c in active])]
    return administrator_operations(account_id, name, same[0] if same else None, active)


def administrator_operations(account_id, name, reuse, former):
    ops = []
    if reuse:
        values = {}
        if person_key(reuse['title']) != 'administrator':
            values['title'] = 'Administrator'
        if reuse['is_active'] != 1:
            values['is_active'] = 1
        if values:
            ops.append(dict(action='update_contact', contact_id=reuse['contact_id'], account_id=account_id, values=values))
    else:
        ops.append(dict(action='create_contact', values=dict(account_id=account_id, name=name,
                        title='Administrator', is_active=1, email=None, phone=None, created_by_candidate=1)))
    for contact in former:
        if not reuse or contact['contact_id'] != reuse['contact_id']:
            ops.append(dict(action='update_contact', contact_id=contact['contact_id'], account_id=account_id,
                            values={'is_active': 0}))
    return ops


def add_updates(p, contacts):
    p = deepcopy(p)
    e = p['supporting_evidence']
    w, a = e.get('website'), e.get('crm')
    if not w or p['classification'] == 'duplicate_resolution':
        return p
    ops = p['proposed_changes']
    new = next((o for o in ops if o['action'] == 'create'), None)
    e['enrichment_version'] = 1
    e['contacts'] = contacts
    # Check the whole linked set: a newly added administrator also makes it stale.
    e['contact_preconditions'] = {a['account_id']: contacts} if a and not new else {}
    if not new:
        fields = {}
        if phone_key(w.get('phone')) and phone_key(w['phone']) != phone_key(a.get('phone')):
            fields['phone'] = w['phone']
        offerings = json.loads(w.get('care_offerings') or '[]')
        if care_key(offerings) and care_key(offerings) != care_key(a.get('care_type')):
            fields['care_type'] = '; '.join(offerings)
        if fields:
            update = next((o for o in ops if o['action'] == 'update'), None)
            if update:
                update['values'].update(fields)
            else:
                ops.append(dict(action='update', account_id=a['account_id'], values=fields))
    if person_key(w.get('administrator')):
        target = {'created_account': new['ref']} if new else a['account_id']
        ops.extend(contact_plan(target, w['administrator'], [] if new else contacts))
    return p


def contact_ops(ops):
    return [o for o in ops if o['action'] in ('create_contact', 'update_contact', 'resolve_administrator')]


def suppress_decided(p, previous):
    """A new extra field must not revive an already decided name/parent edit."""
    p = deepcopy(p)
    ops = p['proposed_changes']
    for old in previous:
        old_ops = json.loads(old['proposed_changes'])
        new_create = next((o for o in ops if o['action'] == 'create'), None)
        old_create = next((o for o in old_ops if o['action'] == 'create'), None)
        if new_create and old_create:
            identity = ('name', 'parent_id', 'billing_street', 'billing_city', 'billing_state')
            if all(new_create['values'].get(k) == old_create['values'].get(k) for k in identity):
                return None  # No approved target exists for extras on a rejected creation.
        for op in ops:
            if op['action'] == 'update':
                settled = {k: v for o in old_ops if o['action'] == 'update' and o['account_id'] == op['account_id'] for k, v in o['values'].items()}
                op['values'] = {k: v for k, v in op['values'].items() if k not in settled or settled[k] != v}
        if contact_ops(ops) and contact_ops(ops) == contact_ops(old_ops):
            ops[:] = [o for o in ops if o not in contact_ops(old_ops)]
    ops[:] = [o for o in ops if o['action'] != 'update' or o['values']]
    return p


def describe(p):
    """Describe actual remaining operations, including after decision suppression."""
    if not p['supporting_evidence'].get('enrichment_version'):
        return p
    ops = p['proposed_changes']
    if any(o['action'] == 'create' for o in ops):
        if contact_ops(ops):
            p['title'] += ' and add its administrator'
        return p
    labels = {'name': 'facility name', 'parent_id': 'parent', 'billing_street': 'address',
              'billing_city': 'address', 'billing_state': 'address', 'billing_zip': 'address',
              'phone': 'phone', 'care_type': 'care offerings'}
    fields = list(dict.fromkeys(labels[k] for o in ops if o['action'] == 'update' for k in o['values'] if k in labels))
    if contact_ops(ops):
        fields.append('administrator')
    extras = any(f in fields for f in ('phone', 'care offerings', 'administrator'))
    if fields and (extras or p['supporting_evidence'].get('decided_changes_omitted')):
        name = (p['supporting_evidence'].get('crm') or p['supporting_evidence']['website'])['name']
        p['title'] = f'Update {", ".join(fields)} for “{name}”'
        if p['classification'] == 'confident_match' or p['supporting_evidence'].get('decided_changes_omitted'):
            p['explanation'] = 'The facility has been matched. The website supplies different or missing information for the fields listed in this proposal.'
        else:
            p['explanation'] += ' The website also supplies current ' + ', '.join(f for f in fields if f in ('phone', 'care offerings', 'administrator')) + ' details where they differ or are missing.'
    return p

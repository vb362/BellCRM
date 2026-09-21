"""Shared wording for generated proposals and saved proposal copy refreshes."""
import re

CHOW_CARD_LABEL = 'Change of ownership with financial data and AR'
CHOW_CLASSIFICATIONS = {'chow_address_match', 'chow_name_match'}
LEGACY_PARENT_SENTENCES = {
    'CRM parent differs or is missing.',
    'The parent recorded in the CRM differs from Bellhaven or is missing.',
}


def parent_sentence(crm):
    """Use the saved parent relationship, even if its display name is unavailable."""
    return ('CRM parent differs from Bellhaven.' if str(crm.get('parent_id') or '').strip()
            else 'CRM parent is missing.')


def proposal_explanation(explanation, evidence):
    return explanation.replace(
        'The address matches, but the name and parent differ.',
        'The address matches, but the facility name needs updating. '
        + parent_sentence(evidence.get('crm') or {}))

MATCHING_SENTENCES = {
    'Names match.': 'The website and CRM record have the same facility name.',
    'Addresses match.': 'The website and CRM record have the same address.',
    'Names and addresses match.': 'The website and CRM record have the same facility name and address.',
    'Names match, but addresses differ.': 'The website and CRM record have the same facility name, but their addresses differ.',
    'Addresses differ; no CRM address matches.': 'Their addresses differ, and no CRM account matches the website address.',
    'Names differ or CRM name is missing.': 'The facility name in the CRM differs from the website name or is missing.',
    'Addresses match, but names differ or CRM name is missing.': 'The website and CRM record have the same address, but the facility name in the CRM differs or is missing.',
    'Addresses match; names match.': 'The website and CRM record have the same facility name and address.',
    'Addresses match; names differ.': 'The website and CRM record have the same address, but their facility names differ.',
    'Addresses match; names are missing in CRM.': 'The website and CRM record have the same address, but the facility name is missing from the CRM.',
    'CRM parent is Bellhaven.': 'The CRM account belongs to Bellhaven.',
    'CRM parent of the record is Bellhaven.': 'The CRM account belongs to Bellhaven.',
    'No CRM name matches.': 'No eligible CRM account matches the facility name on the website.',
    'No CRM address matches.': 'No eligible CRM account matches the facility address on the website.',
    'Required website details are complete.': 'The website provides all the details required to create an account.',
    'No match found on the website by either name or full address (Street + City + State).': 'No website listing matches this CRM account by facility name or by its full street, city, and state address.',
    'Lifetime revenue is zero.': 'The CRM account has no recorded lifetime revenue.',
    'Outstanding AR is zero.': 'The CRM account has no outstanding receivables.',
    'Both lifetime revenue and outstanding AR are zero.': 'The CRM account has no recorded lifetime revenue or outstanding receivables.',
    'Lifetime revenue and outstanding AR are both greater than zero.': 'The CRM account has both recorded lifetime revenue and outstanding receivables.',
    'Website facility matches multiple CRM records.': 'The facility listed on the website matches multiple CRM accounts.',
    'These CRM records have the same normalized name and address.': 'These CRM accounts have the same facility name and address after differences in formatting are accounted for.',
    'These are separate CRM account IDs.': 'Each CRM account has a separate account ID.',
}


def matching_sentence(text):
    if text in MATCHING_SENTENCES:
        return MATCHING_SENTENCES[text]
    text = re.sub(r'(\d+) CRM candidates share this (address|name)',
                  r'There are \1 CRM accounts that share this \2', text)
    text = re.sub(r'(\d+) website facilities share this address',
                  r'There are \1 website facilities that share this address', text)
    return text.replace('; There are', '. There are')


def proposal_copy(classification, title, evidence):
    """Update wording only; retain every matching fact and operation."""
    evidence = dict(evidence)
    if 'bullets' in evidence:
        evidence['bullets'] = [parent_sentence(evidence.get('crm') or {})
                               if text in LEGACY_PARENT_SENTENCES else matching_sentence(text)
                               for text in evidence['bullets']]
    if classification in CHOW_CLASSIFICATIONS:
        name = (evidence.get('website') or evidence.get('crm') or {}).get('name')
        suffix = ' and add its administrator' if title.endswith(' and add its administrator') else ''
        title = CHOW_CARD_LABEL + (f' for “{name}”' if name else '') + suffix
    return title, evidence

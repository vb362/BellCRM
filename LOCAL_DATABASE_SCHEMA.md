# Database schema: local matching and review

This document describes the six new tables needed to pass data between scripts, save proposals, remember decisions, and record approved local changes. No intermediate files are needed.

The SQL definition is in `schema/working.sql`, and `migrate_databases.py` installs it in the working databases. Synchronization with the online CRM is deferred. There is no `sync_attempts` table, `push_status`, or `remote_account_id` in this design.

## 1. Normalized data: the normalizer writes it, the matcher reads it

### `normalized_crm_accounts`

One account as it was read and normalized for a particular run.

| Column | Meaning |
|---|---|
| `run_id` | Run that produced this record |
| `account_id` | Original CRM account ID |
| Original CRM columns listed below | Values as they were when normalization ran |
| `normalized_name` | Name prepared for comparison |
| `normalized_street` | Street prepared for comparison |
| `normalized_city` | City prepared for comparison |
| `normalized_state` | State prepared for comparison |
| `address_fields_present` | True when normalized street, city, and state are all present |

The original CRM columns are copied individually, unchanged:

```text
name, parent_id, parent_name,
billing_street, billing_city, billing_state, billing_zip,
care_type, status, phone,
lifetime_revenue, outstanding_ar,
chow_current_account, duplicate_of_account,
note, created_by_candidate, updated_at
```

**Primary key:** `run_id` + `account_id`.

We keep these original values because approved changes can later modify `crm_accounts`. The matcher and reviewer must still be able to see what was compared at the time.

### `normalized_website_locations`

One website location normalized for a particular run.

| Column | Meaning |
|---|---|
| `run_id` | Run that produced this record |
| `snapshot_id` | Original website collection |
| `source_url` | Location's webpage |
| `normalized_name` | Name prepared for comparison |
| `normalized_street` | Street prepared for comparison |
| `normalized_city` | City prepared for comparison |
| `normalized_state` | State prepared for comparison |
| `address_fields_present` | True when normalized street, city, and state are all present |

**Primary key:** `run_id` + `snapshot_id` + `source_url`.

Original website values remain in `website_snapshots`, linked by `snapshot_id` + `source_url`. Those saved snapshots are preserved, so there is no need to duplicate their original fields here. ZIP is not normalized.

## 2. Proposals and decisions: remember what was suggested and decided

### `proposals`

One distinct suggestion, even if several runs encounter it.

| Column | Meaning |
|---|---|
| `proposal_id` | Proposal's ID; primary key |
| `proposal_key` | Repeatable identifier for the exact suggestion; unique |
| `classification` | Name correction, CHOW, new account, review required, etc. |
| `account_id` | Target account; empty if none exists or has been selected |
| `source_url` | Website location, if available |
| `proposed_changes` | Exact suggested actions, fields, and values |
| `explanation` | Plain-language reason for the suggestion |
| `supporting_evidence` | Compared values, candidate IDs, and references to the original run and source records |
| `created_at` | When the proposal was first saved |

The key identifies the target, action, and exact proposed values. It excludes dates, run IDs, and evidence. The same suggestion therefore produces the same key each time.

- No matching key: create a proposal.
- Matching key without a decision: keep the existing pending proposal.
- Matching key with a decision: skip it, even if supporting evidence changes.

Review-only proposals have no executable changes. Later runs do not overwrite a saved proposal's original evidence.

### `run_proposals`

| Column | Meaning |
|---|---|
| `run_id` | Run that encountered the proposal |
| `proposal_id` | New or existing proposal encountered |

**Primary key:** `run_id` + `proposal_id`.

This records what each run found without creating duplicate proposal cards.

### `decisions`

| Column | Meaning |
|---|---|
| `decision_id` | Decision's ID; primary key |
| `proposal_id` | Proposal being decided; unique |
| `choice` | Approved, rejected, or reviewed without changes |
| `approved_changes` | Exact approved actions, including manual edits; empty for no-change decisions |
| `reviewer_note` | Optional explanation |
| `decided_at` | When the current decision was saved |
| `submitted_at` | When it was submitted; empty beforehand |

There is one current decision per proposal, editable before submission. Approval stages changes; submission applies them. Manual edits do not change the original proposal or its key.

## 3. Local changes and reset

### `change_history`

One attempt to apply a submitted decision locally. A decision can have multiple attempts if an earlier attempt failed.

| Column | Meaning |
|---|---|
| `change_id` | Attempt's ID; primary key |
| `decision_id` | Decision authorizing the changes |
| `attempted_at` | When application was attempted |
| `result` | Succeeded or failed |
| `before_values` | Relevant account IDs and values before the attempt |
| `after_values` | Actual account IDs and values after success, including newly created accounts |
| `error` | Failure explanation, if applicable |

One entry covers the whole approved change. For CHOW, that includes creating the replacement account and linking the old account to it. The approval code performs both actions in one transaction: both succeed, or neither takes effect. No `operation_order` or `depends_on_change_id` columns are needed.

Rejections and reviews without changes remain in `decisions`; they produce no CRM changes. Successfully applied decisions are not applied again. Failed attempts remain visible and can be retried.

Fields such as `proposed_changes`, `supporting_evidence`, and `before_values` hold structured details as validated JSON text inside SQLite. Changes and account snapshots are lists; supporting evidence is an object. These are database values, not separate files passed between scripts. Failed attempts have no `after_values`; successful attempts have no `error`.

Foreign keys connect runs, proposals, decisions, history, and source records. Every database connection must enable `PRAGMA foreign_keys = ON`. The extended `runs` table tracks the website snapshot, scraping/normalization/matching stages, and record/proposal counts. Future matching code must check that its normalization batch is complete before reading it.

The database prevents duplicate proposal keys, duplicate run/proposal links, multiple current decisions for a proposal, and multiple successful applications of a decision. Submitted decisions and application history cannot be edited or deleted. The matcher, approval handler, and reset UI still need to be implemented; the schema alone does not perform those actions.

**Reset:** stop demo work, replace `demo.sqlite` with a safe copy of `start.sqlite`, and create the empty workflow tables in the demo copy. This restores original records and clears proposals and decisions, allowing the original suggestions to appear again. Never modify `start.sqlite` or production during demo reset.

## Matching addition (schema version 3)

`proposals` now also has `title` (the saved card title) and nullable `depends_on_proposal_id` (a foreign key to the duplicate-resolution proposal that must be resolved first). No options table is needed. The matcher prepares ordinary changes for each candidate once and saves a separate duplicate-resolution question.

Migration preserves existing data; older proposals keep a blank title until their existing UI handling supplies one. The matcher never changes CRM records or writes decisions. See [MATCHING_SCRIPT.md](MATCHING_SCRIPT.md) for the current payload, repeat-run behavior, and approval-backend contract.

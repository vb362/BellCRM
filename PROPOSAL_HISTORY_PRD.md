# PRD: Remember decisions and prevent repeated proposals

## 1. Why we need proposal keys, and how they work

The pipeline will run repeatedly. If a reviewer rejects a suggested change, the CRM stays unchanged. Without a saved record of that rejection, the next run would see the same difference and ask the reviewer the same question again.

**We must remember decisions so reviewers do not have to decide the same change twice.** We must also avoid adding another card for a suggestion that is already waiting for review.

To recognize a suggestion, the script gives it a repeatable label called `proposal_key`:

```text
Target account or website location + action + exact proposed values
```

For example:

1. The script suggests changing account 123's name to “Oak Gardens.”
2. It builds the key `123 | update | name=Oak Gardens` and saves the proposal.
3. The reviewer rejects it. The database saves that decision against the proposal.
4. Tomorrow, the script suggests the same change and builds the same key.
5. It finds the saved proposal and rejection, so it does not show another card.

The check happens before adding any proposal:

| What the database contains | What the script does |
|---|---|
| No matching key | Saves a new proposal |
| Matching key, no decision | Keeps the existing pending proposal |
| Matching key with a decision | Skips the suggestion; preserves its history |

**Why this works:** the key comes from the suggestion's contents, so identical suggestions get identical keys. The saved decision survives app restarts. The database also forbids duplicate keys, protecting against two runs trying to insert the same suggestion at once.

Keys must follow these rules:

- Include all proposed operations and values, with fields in a consistent order. Reordering fields must not change the key.
- Exclude dates, run IDs, explanations, and evidence. Otherwise, tomorrow's run could incorrectly look like a new suggestion.
- For a new account, use the website location's URL instead of an account ID. For CHOW, include creation and linking together, using a fixed placeholder for the future account ID.

**A decided change stays decided, even if supporting evidence changes.** A different suggested value, such as “Oak Lodge,” is a different change and can appear for review.

## 2. What we save in the database, and why

We need to answer four separate questions: What was suggested? Which runs found it? What did the reviewer decide? Did the approved change actually succeed?

| Table | Why we need it | Columns |
|---|---|---|
| `proposals` | Remembers the suggestion, its key, and the evidence shown to the reviewer | `proposal_id`, `proposal_key`, `classification`, `account_id`, `source_url`, `operations_json`, `evidence_json`, `created_at` |
| `run_proposals` | Lets several runs refer to the same proposal without making duplicate cards | `run_id`, `proposal_id` |
| `decisions` | Remembers the reviewer's answer so later runs can skip decided suggestions | `decision_id`, `proposal_id`, `choice`, `approved_operations_json`, `reviewer_note`, `decided_at`, `submitted_at` |
| `change_history` | Shows whether applying an approved change succeeded or failed | `change_id`, `decision_id`, `attempted_at`, `result`, `before_json`, `after_json`, `error` |

JSON fields store details together, such as the fields to update and their new values. `account_id` may be empty when no target account exists or has been selected. `source_url` may be empty for an account absent from the website.

The database enforces one proposal per key, one current decision per proposal, and one link per run/proposal pair. Foreign keys keep these related records connected. Each mode stores its own records in its own database.

Following the existing interface, approval stages changes; submission applies them. Decisions can be edited before submission. Manual edits are saved in `approved_operations_json`, leaving the original proposal and its identifying key intact. Rejection and review without changes do not modify CRM records.

**Approval and success are separate because an approved change can fail to save.** A failure stays attached to the same decision and can be retried without generating a new proposal. In test mode, each proposal's CRM changes and success record are saved together in one transaction: all succeed, or none take effect.

## 3. Why reset clears this history, and why it works

Test mode must let us demonstrate the process again from the beginning. Restoring only the original CRM records would not be enough: old decisions would still hide the proposals we want to demonstrate.

**Reset must restore the starting data and clear the demo's memory of decisions together.**

1. Stop demo runs and submissions, block new work, and close database connections so nothing writes during reset.
2. Safely replace `demo.sqlite` with a copy of `start.sqlite`.
3. Create empty workflow tables in the demo copy, including `runs`, then reopen it.

This works because the replacement removes both the edited CRM data and all demo proposals, keys, decisions, and change history. The next run sees the original differences and finds no previous decisions, so it can show those proposals again.

| User action | Expected result |
|---|---|
| Run again or restart the app | Pending cards are not duplicated; decided suggestions stay hidden |
| Retry a failed approved change | Reuse its existing proposal and decision |
| Reset test mode, then run again | Original proposals can appear again |

These behaviors must be tested. Reset must also leave `start.sqlite`, `production.sqlite`, and the online CRM untouched. It makes no CRM API calls and never clears production decisions.

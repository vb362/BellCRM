# Product Requirements Document

## Demo Mode and Production Mode

### Why we have a demo mode

The assessment requires two deliverables: a working app that can be demonstrated live, and a corrected online CRM with the approved changes actually saved through the API.

To correct the online CRM while keeping the ability to demonstrate the whole process from the CRM’s original state, I split the app into two modes: demo and production. A reset button restores only the demo to its starting state, leaving the corrected online CRM and real work history untouched.

### 1. Overview: one app, two modes, one reset button

The app has two modes:

- **Demo mode:** reads and changes a local copy of the initial CRM state. Used to practice and demonstrate the full process live, from start to finish.
- **Production mode:** reads and changes the assessment’s online CRM through its API. Used to complete the actual submission.

Both modes use the same matching rules and review process.

**A reset button allows us to restart the demo from scratch—the initial state of the CRM and website—to showcase it in a live demo.**

### 2. Database files

We store the data in three SQLite files.

| File | Purpose | Does it change? |
|---|---|---|
| `start.sqlite` | Untouched starting data used to reset the demo | No, once prepared |
| `demo.sqlite` | Current demo records, decisions, and history | Yes, as the demo runs |
| `production.sqlite` | Copies of real data, real decisions, and history | Yes, as real work runs |

When the reset button is pressed, the app replaces `demo.sqlite` with a copy of `start.sqlite`.

This allows us to demonstrate the full process from the beginning, before any edits were made to the CRM. Between resets, the demo remembers previous edits and decisions, so running it again does not propose the same decided changes. Only a deliberate demo reset clears the demo’s decisions.

### 3. Overview of the tables

`start.sqlite` and `demo.sqlite` have the same table structure, as resetting the demo copies one into the other.

`production.sqlite` is slightly different: the actual CRM records live on their server, so it stores fetched copies instead of editable local accounts.

| Table | In `start.sqlite` | In `demo.sqlite` |
|---|---|---|
| `crm_accounts` | Original CRM records, captured before edits | Local records that approved demo changes can update |
| `website_snapshots` | Saved website pages and extracted locations | Starting website data plus data from later demo runs |
| `runs` | Empty | Each demo run and its result |
| `proposals` | Empty | Suggested demo changes |
| `decisions` | Empty | Demo approvals and rejections |
| `change_history` | Empty | Demo changes attempted, their results, and before/after values |

`production.sqlite` contains:

| Table | Contents |
|---|---|
| `crm_snapshots` | Copies of CRM records fetched through the API |
| `website_snapshots` | Website pages and extracted locations collected over time |
| `runs` | Each real run and its result |
| `proposals` | Suggested real changes |
| `decisions` | Real approvals and rejections |
| `change_history` | API changes attempted, their results, and before/after values |

In demo mode, approved changes update `crm_accounts`.

In production mode, approved changes go through the API. The app then checks the online result and saves a fresh local copy.

### 4. Demo reset

The reset button:

1. Stops demo work that is currently running.
2. Replaces `demo.sqlite` with a complete copy of `start.sqlite`.
3. Opens the restored demo database.

This restores the original accounts and saved website data. It clears demo runs, proposals, decisions, and change history.

The reset must never:

- Change `start.sqlite`.
- Change `production.sqlite`.
- Send changes to the online CRM.

The app must enforce this rule internally, as well as showing the reset button only in demo mode.

### 5. GitHub organization

```text
data/
  start.sqlite
  demo.sqlite
  production.sqlite
```

| File | GitHub rule |
|---|---|
| `start.sqlite` | Included as the fixed demo starting point |
| `demo.sqlite` | Excluded; created locally from `start.sqlite` |
| `production.sqlite` | Included as a saved copy when the submission is ready |

During normal work, production data changes locally. The app does not automatically commit database changes to GitHub.

Before submitting:

1. Finish applying approved changes through the API.
2. Fetch and check the corrected online records.
3. Save a complete, consistent copy of `production.sqlite` in GitHub.
4. Confirm the included databases contain no API tokens or other secrets.

There is no separate `finished.sqlite` file.

Database copies must be made safely so they include all saved changes. Code for creating and updating the database tables is also included in GitHub.


Bellhaven normalization rules

These are the rules currently implemented in `normalize_data.py`.

## 1. Purpose

Create consistent versions of facility names and addresses so CRM records and website records can be compared later.

- Apply the same rules to both sources.
- Preserve the original values beside the normalized values.
- Save comparison records and normalization tracking in the working database. Never change original CRM records, website snapshots, or `start.sqlite`.
- Use explicit dictionaries and positional rules, with no trained model or external service.
- Do not match facilities, merge accounts, or decide which record is correct.

**An identical normalized address is evidence for a possible match. It does not prove that two records represent the same facility.**

## 2. Common text cleanup

Apply these steps to facility names, streets, cities, and states:

1. Keep a missing value as missing (`None` in Python).
2. Convert any other value to text and lowercase it.
3. Split the text at whitespace, including spaces, tabs, and line breaks.
4. Join the resulting words with one ordinary space. This also removes leading and trailing whitespace.
5. If nothing remains, return a missing value.

Example: `  MAIN   Street  ` becomes `main street` before street-specific rules run.

### What counts as a word?

A word is one whitespace-separated element. Replacements require an exact match to the entire element.

- `Willow`, `Westchester`, and `Astoria` are never changed internally.
- Punctuation and accents are preserved.
- `street.` is different from `street`; `rehab,` is different from `rehab`.
- Literal text such as `N/A` is lowercased, not converted to a missing value.

## 3. Facility names

After common cleanup, scan the words from left to right. First replace the adjacent two-word phrase `health care`; otherwise apply the single-word dictionary.

| Exact input | Replacement |
| --- | --- |
| `health care` | `healthcare` |
| `centre` | `center` |
| `rehab` | `rehabilitation` |
| `&` | `and` |

All other words and punctuation remain. In particular, retain brands, `the`, `at`, `of`, `nursing`, `care`, `manor`, and `campus`. Hyphens are not removed.

| Original | Normalized |
| --- | --- |
| `Bellhaven Health Care Centre` | `bellhaven healthcare center` |
| `Bellhaven Rehab & Nursing` | `bellhaven rehabilitation and nursing` |
| `The Arbors at Bellhaven - Dayton` | `the arbors at bellhaven - dayton` |

These rules do not treat `Bellhaven at Sycamore Ridge` and `Bellhaven of Sycamore Ridge` as identical names.

## 4. Street addresses

Apply common cleanup first, then the following steps in order.

### Step A — Locate the street suffix

A suffix is a street type, such as `road` or `street`.

1. Inspect the last word.
2. If it is a direction from the direction table below, or one of its abbreviations, inspect the preceding word instead.
3. The inspected word must be one of these exact values:

   `road`, `rd`, `street`, `st`, `avenue`, `ave`, `boulevard`, `blvd`, `drive`, `dr`, `lane`, `ln`, `pike`, `pk`.

4. If there is no word at that position, or it is not in this list, stop street-specific processing. Keep only the common text cleanup.

The script does not search other positions for a suffix.

### Step B — Normalize the suffix

At the position identified in Step A, apply this dictionary:

| Exact input | Replacement |
| --- | --- |
| `road` | `rd` |
| `street` | `st` |
| `avenue` | `ave` |
| `boulevard` | `blvd` |
| `drive` | `dr` |
| `lane` | `ln` |
| `pk` | `pike` |

Suffixes already in the replacement column remain unchanged. `pk` becomes `pike` to reconcile the variation observed between our datasets. This rule applies to every street at the suffix position identified in Step A, not only Wilmington.

This step does not require a numeric first word.

### Step C — Abbreviate directions

| Exact input | Replacement |
| --- | --- |
| `north` | `n` |
| `south` | `s` |
| `east` | `e` |
| `west` | `w` |
| `northeast` | `ne` |
| `northwest` | `nw` |
| `southeast` | `se` |
| `southwest` | `sw` |

**First-word requirement:** direction replacements run only if the first word consists entirely of the digits `0` through `9`. Otherwise, keep directions unchanged. Suffix replacements from Step B still apply.

For addresses that meet this requirement:

- **Final direction:** abbreviate the last word if it is a direction immediately after the recognized suffix, with at least one word between the number and that suffix.
- **Initial direction:** abbreviate the word immediately after the number if it is a full direction word, with at least one additional word between it and the recognized suffix. A final direction does not affect this check.
- Leave directions in all other positions unchanged. Already abbreviated directions remain unchanged.

These are checks of word positions, not an interpretation of the official street name.

### Street examples

| Original | Normalized | Reason |
| --- | --- | --- |
| `123 West Main Street` | `123 w main st` | Initial direction and final suffix. |
| `123 West Street` | `123 west st` | No separate word between `west` and the suffix. |
| `123 Main Street West` | `123 main st w` | Direction immediately after the suffix. |
| `123 Main West Street` | `123 main west st` | `west` is neither the initial nor final direction. |
| `123 North Main Street West` | `123 n main st w` | Both direction rules apply. |
| `123 Westchester Drive` | `123 westchester dr` | No replacement inside `westchester`. |
| `45 St Lawrence Dr` | `45 st lawrence dr` | Internal `st` remains part of the street name. |
| `3313 Wilmington Pk` | `3313 wilmington pike` | Treat `pk` and `pike` as equivalent suffixes. |
| `123A West Main Street` | `123a west main st` | First word contains a letter; only the suffix changes. |
| `123-125 West Main Street` | `123-125 west main st` | First word contains a hyphen; only the suffix changes. |
| `123 West Main Street Apt 4` | `123 west main street apt 4` | No recognized suffix at the inspected position. |
| `123 Main Street.` | `123 main street.` | Punctuation prevents an exact suffix match. |
| `PO Box 517` | `po box 517` | Common cleanup only. |

## 5. Cities, states, and ZIP codes

| Field | Treatment |
| --- | --- |
| City | Common text cleanup only. No spelling corrections or city aliases. |
| State | Common text cleanup only. Full state names are not converted to abbreviations. |
| ZIP | Preserved exactly as read from the database. No trimming, padding, formatting, or corrections. |

For example, `Ohio` becomes `ohio`, while `OH` becomes `oh`. They remain different.

## 6. Output fields and missing addresses

Each saved comparison row contains:

| Added field | Meaning |
| --- | --- |
| `normalized_name` | Facility name after the name rules. |
| `normalized_street` | Street after the street rules. |
| `normalized_city` | City after common cleanup. |
| `normalized_state` | State after common cleanup. |
| `address_fields_present` | `True` only when normalized street, city, and state are all non-missing. |

Missing normalized values are stored as SQL `NULL`. Original CRM fields are copied into `normalized_crm_accounts`; original website fields remain in the linked `website_snapshots` record. ZIP is retained unchanged; there is no normalized ZIP field. The script writes no CSV files. See `NORMALIZATION_RULES.md` for the explicit-run command and database output behavior.

**`address_fields_present` checks presence only.** It does not validate an address, recognize unsupported formats, or measure confidence.

For the later matching stage, the agreed address comparison uses **street + city + state together**, with all three present. ZIP differences alone do not trigger proposals. The normalization script does not implement that matching stage.

## 7. Limits of these rules

- They do not cover every US address format.
- They do not verify that an address exists.
- They do not correct typos, distinguish a billing address from a physical address, or detect relocations.
- They do not split or remove apartment, unit, or building information.
- They do not automatically flag every unsupported or ambiguous address. Review the original and normalized columns together.
- Shared addresses, different brands, campus entities, and ownership changes require separate matching decisions.

The same input produces the same output with this implementation. Any additional equivalence must be added explicitly to the rules.


# Bellhaven matching rules

The current matching conditions, exact card wording, CHOW handling, and duplicate workflow are maintained in [MATCHING_RULES.md](MATCHING_RULES.md). That document replaces the earlier matching table here and includes the agreed name-only address-and-parent correction. ZIP-only corrections are excluded.

The implementation and database handoff are documented in [MATCHING_SCRIPT.md](MATCHING_SCRIPT.md).

PRD: Remember decisions and prevent repeated proposals

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

Duplicate treatment

If a website facility matches two CRM accounts with the same name and address:
The matching script saves one duplicate-resolution proposal and a correction proposal for each account that needs website updates. It links the correction proposals to the duplicate proposal. This happens in one matching run.
In the UI:
Show the duplicate-resolution card first. The correction proposals already exist in the database, but remain hidden in the UI. The reviewer chooses the survivor, adds a note, and submits.
When that decision is submitted:
The backend performs one database transaction:
1. Records the chosen survivor, loser, and reviewer note.
2. Marks the loser Inactive and sets its duplicate_of_account to the survivor’s ID.
3. Dismisses the loser’s correction proposal with the reason “Account resolved as a duplicate.”
All three succeed together, or none of them are saved.
After that transaction succeeds:
The UI reveals the survivor’s already-prepared correction proposal. The reviewer approves or rejects it separately. If approved, the backend applies those corrections and records the result in another transaction. Matching does not run again.
The backend enforces the same order:
- If duplicate resolution is pending: neither correction can be applied.
- If an account is the loser: its correction cannot be applied.
- If an account is the survivor: its correction becomes available for review. Before applying it, verify that the data used to prepare it has not changed.
On future runs, the script skips linked losers and uses saved decisions to avoid repeating resolved proposals.

# Matching script: input, output, and use

`match_records.py` applies [MATCHING_RULES.md](MATCHING_RULES.md). It reads one saved normalized batch and writes proposals into the same SQLite database. It does not scrape, normalize again, modify CRM accounts, or call the CRM API. Confident matches with no operations are automatically recorded as submitted no-change decisions after dependency and freshness checks. All actual changes still require human review.

## Full workflow

The normal entry point is `.venv/bin/python run_pipeline.py --database demo`. It calls scraping, normalization, and this matcher in order. See [PIPELINE.md](PIPELINE.md).

## Individual-stage use (for testing)

Each script also remains independently runnable. From the project directory:

```sh
python3 migrate_databases.py --database demo
python3 normalize_data.py --database demo --run-id YOUR_RUN_ID
python3 match_records.py --database demo --run-id YOUR_RUN_ID
```

Use the run ID printed by the existing scraper, or an existing completed scrape in `runs`. Production uses the same commands with `--database production`; it must have its own CRM data and completed scrape. `start.sqlite` cannot be used as a writable target.

The pipeline calls `match_database(database_path, run_id)` directly. The script requires schema version 3, a completed scrape, and completed normalization for that exact run. It refuses inconsistent batches, stale CRM input, or broken/circular account links. It never substitutes an older scrape.

## What it saves

- `proposals`: stable key, classification, saved card title, explanation, structured evidence, and proposed operations. `depends_on_proposal_id` links a candidate result to its duplicate-resolution question.
- `run_proposals`: which proposals the run encountered, including existing and already decided proposals.
- `runs`: matching status, counts, and issues. A failed batch rolls back all its proposal/link writes, then records a failure. Incomplete individual website records and insufficient financial data are reported here. Overall pipeline status is recorded by `run_pipeline.py`.

Matching holds one SQLite transaction, so concurrent calls cannot create duplicate batches. Repeating a completed run reuses its recorded result. A new run compares its saved input, reuses pending proposals by key, and counts already submitted decisions as skipped. An existing pending proposal with no staged decision keeps its ID and receives fresh evidence. Once a decision is staged or submitted, the reviewed evidence is preserved; stale assumptions block application until the reviewer revisits the case.

A correction key hashes the target and proposed operations. It excludes run ID, timestamps, explanation, and evidence. Creates use the source URL as their target; CHOW uses the old account ID and a stable placeholder for the future ID. A duplicate question is keyed by sorted account IDs. A different group or materially different proposed changes can produce a new proposal. Staged, unsubmitted decisions do not count as final decisions.

`supporting_evidence` contains the original website values, original CRM values, normalized matching values, candidate IDs/URLs, up to three normal bullets plus a fourth ambiguity bullet, and original CRM preconditions for later approval checks. Financial values and last-updated dates are retained for duplicate selection. Full field comparisons can be built without scraping again.

## Operation contract for the approval backend

`proposed_changes` is a list of operations inside SQLite, not an exported file:

| Action | Meaning |
| --- | --- |
| `update` | Change the named account's specified fields only. |
| `create` | Create an account from `values`; keep the generated ID under the given `ref`. Website care offerings are retained as semicolon-separated labels in `care_type`; phone and address are copied on creation. Administrator contacts and corrections to existing account phone/care fields are prepared by the additional-update step below. |
| `append_note` | Append the supplied text without erasing an existing CRM note. |
| `resolve_duplicates` | A human-choice question, not an executable update. The backend validates the survivor and phone selection, requires a note, and expands the choice into contact preservation, the selected phone update, and loser status/link/note updates. |

A CHOW proposal creates `ref: new_account`, then updates only the old account's `chow_current_account` using `{"created_account":"new_account"}`. The approval backend must resolve that reference and commit both operations together. It must not copy old revenue/AR into the replacement.

Duplicate resolution and correction proposals are separate rows. Correction results stay hidden and blocked until resolution is submitted and successfully applied. Duplicate evidence includes the entire contact set for every candidate; changes to contacts or facility phones invalidate the review. On approval, `duplicate_resolution.py` prepares contact copies with new IDs (preserving business details and active status), original-contact deactivation, the reviewed facility phone, then loser status/link/note updates. The API executor verifies each copy before proceeding. Local submission is atomic; production can pause after partial success. Successful completion dismisses all pending loser corrections and refreshes the survivor's unreviewed contact operations, removing its already-resolved phone edit. Existing account-only drafts require reapproval. Explicit keep-both/all is a submitted `reviewed` decision with no operations and a reviewer note; simple rejection does not release corrections.

`proposal_review_state(connection, proposal_id)` provides the read-only dependency gate: `ready`, `blocked`, `decided`, or `superseded`. It requires `sqlite3.Row` connections. It also hides old duplicate questions superseded by a larger group. `assert_proposal_applicable` additionally rejects stale relevant CRM values and must be called inside the future approval transaction. The backend must call these guards for submission, not rely on UI visibility. It must still validate operation payloads, reviewer selection, current candidate membership, parent targets, and conflicting changes before writing. These helpers do not implement an approval endpoint or make the current mock UI enforce anything by themselves.

The local UI and approval service now use these guards through `server.py` and `review_service.py`; see [WEB_APP.md](WEB_APP.md). Reset, scheduling, and production synchronization remain separate work.

Checks use temporary databases:

```sh
python3 -B -m unittest discover -s tests -v
```

## Additional website updates

After the existing matching rules, `supplemental_updates.py` compares facility phone,
care offerings and the administrator. This happens inside the same matching run.
Phone comparison ignores formatting and the US country prefix but preserves extensions.
Care comparison treats offerings as sets, ignoring case, whitespace, ordering and list
separators. Website labels are retained; no care synonyms are silently assumed.
Empty website information never clears CRM values.

A perfect name/address/parent match remains a human-reviewed proposal when any additional
change is needed. Only an empty operations list is eligible for automatic completion.
Account corrections bundle extras with the existing updates. New and CHOW accounts receive
website phone/care and an Administrator contact; old CHOW contacts are left unchanged.
Absence proposals have no additional website updates. Duplicate-dependent extras stay
blocked until resolution, and loser proposals are dismissed through the existing flow.

Contacts are matched within the selected account, by role and personal name (case/whitespace,
not facility-name substitutions). An existing active matching Administrator needs no edit.
A matching person under another role is reused and, if necessary, reactivated. Otherwise
create a new Administrator contact and deactivate the sole former administrator. Preserve
former names, emails and phones. Do not copy the facility phone into a contact's phone.
Multiple matching contacts or active administrators require an explicit reviewer selection.

The operations list also supports `create_contact`, `update_contact`, and the human-choice
placeholder `resolve_administrator`. New contacts can reference a created account using
`{"created_account":"new_account"}`. Contact snapshots live in supporting evidence;
changes to the linked contact set invalidate approval. The local backend resolves choices,
validates targets, and commits account/contact changes, decision and audit history together.
No schema migration is needed. Remote contact write payloads still need production validation.

New extras do not revive previously decided account fields or contact operations. Unreviewed
older cards are superseded by their replacement; staged decisions retain their original
operations and evidence. A later run picks up additional updates after a draft is settled.
